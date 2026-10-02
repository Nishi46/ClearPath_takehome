import re
from pathlib import Path

import pytest

from app import db
from tests.chrome_layout import CHROME, measure

needs_chrome = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")
CSS = Path("app/static/style.css").read_text()


def page(client, sid, role="reviewer"):
    client.cookies.set("role", role)
    return client.get(f"/review/{sid}").text


# ---- structure, landmarks, names (no browser) ----

@pytest.mark.parametrize("sid", range(1, 15))
def test_headings_and_landmarks(client, sid):
    html = page(client, sid)
    assert len(re.findall(r"<h1[ >]", html)) == 1
    h2s = re.findall(r'<h2 id="([\w-]+)"', html)
    assert h2s[:2] == ["copy-heading", "flags-heading"] and "history-heading" in h2s
    for hid in h2s:                                         # every h2 labels exactly one section
        assert html.count(f'aria-labelledby="{hid}"') == 1
    assert html.count("<main") == 1 and html.count('<aside class="review-side" aria-label="') == 1
    for region in re.findall(r'<div class="review-(?:main|side-scroll)"[^>]*>', html):
        assert 'role="region"' in region and "aria-label=" in region and 'tabindex="0"' in region


@pytest.mark.parametrize("sid", [1, 3, 6])
def test_focus_order_follows_reading_order(client, sid):
    html = page(client, sid)
    marks = ["Back to the queue", 'class="version-nav"', 'id="copy-heading"', 'id="flags-heading"',
             'id="comments-heading"', 'id="history-heading"']
    if sid != 6:
        marks.insert(5, 'id="decision-heading"')
    positions = [html.index(m) for m in marks]
    assert positions == sorted(positions)


def test_every_control_has_an_accessible_name(client):
    html = page(client, 3)
    assert 'for="decision-reason"' in html and 'id="decision-reason"' in html
    for button in re.findall(r"<button[^>]*>(.*?)</button>", html.split('class="decision-form"')[1], re.S):
        assert button.strip()
    for link in re.findall(r"<a [^>]*>(.*?)</a>", html, re.S):
        assert re.sub(r"<[^>]+>", "", link).strip()


def test_every_flag_anchor_has_a_target(client):
    for sid in range(1, 15):
        html = page(client, sid)
        for href in re.findall(r'href="#(flag-\d+)"', html):
            assert html.count(f'id="{href}"') == 1


def test_css_rules_for_layout_and_motion():
    assert "@media (min-width: 60rem)" in CSS and "grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)" in CSS
    assert re.search(r"\.review-main[^{]*\{[^}]*overflow: auto", CSS) or "overflow: auto" in CSS.split("@media (min-width: 60rem)")[-1]
    assert "overflow-wrap: anywhere" in CSS
    assert "@media (prefers-reduced-motion: no-preference)" in CSS
    assert re.search(r"\[tabindex\]:focus-visible", CSS) and "outline: 3px solid var(--focus)" in CSS
    # The pulse is only ever inside the reduced-motion guard.
    outside = re.sub(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", "", CSS, flags=re.S)
    assert "animation: flag-pulse" not in outside


# ---- real browser measurements ----

@needs_chrome
@pytest.mark.parametrize("sid", [1, 3, 12])
@pytest.mark.parametrize("size", [(1366, 768), (1440, 900)])
def test_laptop_copy_flags_and_decision_are_on_screen_together(client, sid, size):
    w, h = size
    m = measure(page(client, sid), w, h)
    assert m["scrollWidth"] <= m["clientWidth"]
    assert 0 <= m[".review-head h1"]["top"] < h
    assert m[".copy-text"]["top"] < h * 0.45                           # the copy starts in the upper half
    assert m[".flag-card"]["top"] < h and m[".flag-card"]["bottom"] <= m[".decision-pane"]["top"] + 1
    assert m[".decision-buttons"]["bottom"] <= h                       # the buttons are visible without scrolling
    assert m[".decision-pane"]["bottom"] <= h
    assert m[".flags-pane h2"]["top"] < m[".decision-pane"]["top"]
    side, main = m[".review-side-scroll"], m[".review-main"]
    assert main["left"] < side["left"]                                  # copy left, flags right
    if sid == 12:
        assert main["sh"] > main["ch"]                                  # the long copy scrolls inside its pane


@needs_chrome
def test_locked_item_uses_the_whole_right_column_for_flags(client):
    m = measure(page(client, 14), 1366, 768)
    assert m[".decision-pane"] is None and m[".flag-card"]["top"] < 768
    assert m[".review-side-scroll"]["bottom"] <= 768 and m[".review-main"]["bottom"] <= 768


@needs_chrome
@pytest.mark.parametrize("path", ["/review/7?v=1", "/review/6", "/review/5?v=1"])
def test_pages_with_a_banner_still_fit_the_columns_on_a_laptop(client, path):
    client.cookies.set("role", "reviewer")
    m = measure(client.get(path).text, 1366, 768)
    assert m[".review-main"]["bottom"] <= 768 and m[".review-side-scroll"]["bottom"] <= 768
    assert m[".copy-text"]["top"] < 768 / 2 + 100


@needs_chrome
@pytest.mark.parametrize("sid", range(1, 15))
def test_phone_width_has_no_sideways_scroll_and_stacks_in_order(client, sid):
    m = measure(page(client, sid), 390, 844)
    assert m["scrollWidth"] <= m["clientWidth"], m["wide"]
    assert m["wide"] == []
    assert m[".copy-pane h2"]["top"] < m[".flags-pane h2"]["top"]
    if m[".decision-pane"]:
        assert m[".flags-pane h2"]["top"] < m[".decision-pane"]["top"]
        assert m[".decision-buttons"]["right"] <= 390


@needs_chrome
def test_long_unbroken_strings_do_not_widen_the_page(client):
    long = "x" * 5000
    with db.connect() as c:
        c.execute("UPDATE version SET copy = ?, notes = ? WHERE submission_id = 6", (long, long))
        c.execute("UPDATE submission SET title = ? WHERE id = 6", ("T" * 300,))
        c.execute("UPDATE decision SET reason = ?, reviewer = ? WHERE submission_id = 6", (long, "R" * 300))
        c.execute("UPDATE comment SET text = ? WHERE submission_id = 6", (long,))
    for w, h in ((390, 844), (1366, 768)):
        m = measure(page(client, 6), w, h)
        assert m["scrollWidth"] <= m["clientWidth"] and m["wide"] == [], (w, m["wide"])


@needs_chrome
def test_the_error_banner_page_keeps_the_decision_buttons_on_screen(client):
    from tests.test_review_decision_route import post
    r = post(client, outcome="rejected", reason="   ")
    assert r.status_code == 422
    m = measure(r.text, 1366, 768)
    assert m[".decision-buttons"]["bottom"] <= 768 and m[".review-side-scroll"]["bottom"] <= 768
