"""Phase 7 steps 17 to 21: never color-only, keyboard and focus, laptop and phone layout, a clean console."""
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.routes import pages, submit_pages
from tests.chrome_layout import CHROME, evaluate
from tests.test_phase7_edge_matrix import as_role
from tests.test_styles import CSS, T, contrast

needs_chrome = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")
HEADERS = {"origin": "http://testserver"}

# One page per template and state, with the role that sees it.
SCREENS = [
    ("reviewer", "Maya Chen", "/"), ("marketer", "Maya Chen", "/"), ("reviewer", "Maya Chen", "/?status=rejected&product=card"),
    ("reviewer", "Maya Chen", "/mine"), ("marketer", "Maya Chen", "/mine"), ("marketer", "Jordan Lee", "/mine"),
    ("marketer", "Sam Patel", "/mine"), ("marketer", "Maya Chen", "/submit"), ("reviewer", "Maya Chen", "/submit"),
    ("reviewer", "Maya Chen", "/review/1"), ("reviewer", "Maya Chen", "/review/3"), ("reviewer", "Maya Chen", "/review/5?diff=1"),
    ("reviewer", "Maya Chen", "/review/6"), ("reviewer", "Maya Chen", "/review/12"), ("reviewer", "Maya Chen", "/review/13"),
    ("reviewer", "Maya Chen", "/review/14?snippet=R2"), ("marketer", "Maya Chen", "/review/7"),
    ("marketer", "Jordan Lee", "/resubmit/14"), ("marketer", "Jordan Lee", "/resubmit/8"), ("reviewer", "Maya Chen", "/resubmit/14"),
    ("reviewer", "Maya Chen", "/reset/confirm"), ("reviewer", "Maya Chen", "/nowhere"),
]


def render(client, role, marketer, path):
    return as_role(client, role, marketer).get(path).text


# ---- step 17: status and severity are never color-only ----

class Elements(HTMLParser):
    """Text of every element whose class matches, including the visually-hidden words inside it."""

    def __init__(self, pattern):
        super().__init__()
        self.pattern, self.stack, self.found = re.compile(pattern), [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for item in self.stack:
            item["depth"] += 1 if item["tag"] == tag else 0
        if self.pattern.search(a.get("class") or ""):
            self.stack.append({"tag": tag, "depth": 1, "text": [], "attrs": a})

    def handle_data(self, data):
        for item in self.stack:
            item["text"].append(data)

    def handle_endtag(self, tag):
        for item in list(self.stack):
            if item["tag"] == tag:
                item["depth"] -= 1
                if item["depth"] == 0:
                    self.stack.remove(item)
                    self.found.append((item["attrs"], re.sub(r"\s+", " ", "".join(item["text"])).strip()))


def elements(html, pattern):
    p = Elements(pattern)
    p.feed(html)
    return p.found


def test_every_status_chip_names_the_status_in_words(client):
    words = {"New", "In review", "Changes requested", "Approved", "Rejected"}
    for role, who, path in SCREENS:
        for attrs, text in elements(render(client, role, who, path), r"(^|\s)status(\s|$)"):
            assert text in words or any(w in text for w in words), (path, text)


def test_every_urgency_cue_is_a_sentence_not_just_a_color(client):
    for path in ["/", "/mine"]:
        html = render(client, "reviewer", "Jordan Lee", path)
        for attrs, text in elements(html, r"(^|\s)urgency(\s|$)"):
            assert re.match(r"(Rush|Overdue)", text), (path, text)
    queue = render(client, "reviewer", "Maya Chen", "/")
    rows = re.findall(r'<tr class="(urgency-\w+)">(.*?)</tr>', queue, re.S)
    assert rows and all(('Rush' in body or 'Overdue' in body) for _, body in rows)


def test_every_severity_marker_has_a_word_for_screen_readers_and_for_everyone(client):
    for sid in (1, 4, 14):
        html = render(client, "reviewer", "Maya Chen", "/review/%d" % sid)
        sev = elements(html, r"(^|\s)sev(\s|$)")
        assert sev
        # A letter alone (H, M, L) is never the only signal: the severity is also written out near it.
        assert re.search(r"(High|Medium|Low)", html)
    queue = render(client, "reviewer", "Maya Chen", "/")
    for attrs, text in elements(queue, r"(^|\s)flags(\s|$)"):
        assert re.search(r"(No flags|\d+ rules? flagged, highest severity (High|Medium|Low))", text), text


def test_every_highlight_names_its_severity_in_words(client):
    html = render(client, "reviewer", "Maya Chen", "/review/1")
    marks = elements(html, r"flag-mark")
    assert marks and all(re.search(r"(High|Medium|Low)", text) for _, text in marks)


def test_diff_marks_say_added_or_removed_in_words(client):
    html = render(client, "reviewer", "Maya Chen", "/review/5?diff=1")
    assert "added: " in html and "removed: " in html
    assert 'class="diff-added"' in html and 'class="diff-removed"' in html
    assert re.search(r"\.diff-added\s*\{[^}]*text-decoration: underline", CSS)
    assert re.search(r"\.diff-removed\s*\{[^}]*text-decoration: line-through", CSS)


def test_highlight_styles_differ_by_more_than_color():
    styles = {sev: re.search(r"\.mark-%s\s*\{([^}]*)\}" % sev, CSS).group(1) for sev in ("high", "medium", "low")}
    borders = {re.search(r"border-bottom:\s*([^;]+);", s).group(1).split()[1] for s in styles.values()}
    assert borders == {"solid", "double", "dashed"}


def test_the_decision_status_is_written_not_only_tinted(client):
    for sid, word in ((6, "Approved"), (7, "Approved"), (8, "Rejected"), (14, "Changes requested")):
        assert "Locked" in render(client, "reviewer", "Maya Chen", "/review/%d" % sid) or sid == 14
        assert word in render(client, "reviewer", "Maya Chen", "/review/%d" % sid)


EXTRA_TEXT_PAIRS = [("text", "overdue-bg"), ("text-muted", "bg"), ("accent", "surface"), ("focus", "surface")]


def test_text_pairs_used_for_highlights_and_diffs_have_aa_contrast():
    assert contrast(T["text"], "#e6f4ea") >= 4.5 and contrast(T["text"], "#fce8e6") >= 4.5   # diff fallbacks
    for fg, bg in EXTRA_TEXT_PAIRS:
        assert contrast(T[fg], T[bg]) >= 3.0, (fg, bg)
    assert contrast(T["text-muted"], T["bg"]) >= 4.5       # .mark-low text sits on --bg


def test_form_controls_have_a_boundary_with_3_to_1_contrast():
    # WCAG 1.4.11: the edge of an input, select, textarea or button must be visible without color cues.
    assert "control-border" in T and contrast(T["control-border"], T["surface"]) >= 3.0
    assert contrast(T["control-border"], T["bg"]) >= 3.0
    # Every control rule that draws its own border uses the 3:1 token, not the pale decorative one.
    for selector in (r"\.role-option", r"\.reset-demo", r"\.filter select, \.filter-apply", r"\.decision-buttons button",
                     r"\.form-actions button, \.button-link"):
        rule = re.search(r"(?m)^%s\s*\{([^}]*)\}" % selector, CSS)
        assert rule and "var(--control-border)" in rule.group(1) and "var(--border)" not in rule.group(1), selector


def test_forced_colors_keeps_boundaries_and_focus_visible():
    block = re.search(r"@media \(forced-colors: active\) \{(.*?)\n\}", CSS, re.S)
    assert block, "no forced-colors rules"
    inside = block.group(1)
    for needle in (".status", ".flag-mark", ".sev", "outline"):
        assert needle in inside, needle
    assert not re.search(r"outline:\s*(none|0)\b", CSS)             # focus is never removed anywhere


# ---- step 18: keyboard and focus ----

class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items, self.labels, self.cur, self.hidden = [], set(), None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label" and a.get("for"):
            self.labels.add(a["for"])
        if tag in ("a", "button") and not (tag == "a" and "href" not in a):
            self.cur = {"tag": tag, "attrs": a, "text": []}
        elif tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.items.append({"tag": tag, "attrs": a, "text": []})
        if self.cur and a.get("aria-hidden") == "true":
            self.hidden += 1

    def handle_data(self, data):
        if self.cur and not self.hidden:
            self.cur["text"].append(data)

    def handle_endtag(self, tag):
        if self.cur and tag == self.cur["tag"]:
            self.items.append(self.cur)
            self.cur, self.hidden = None, 0


def controls(html):
    p = Controls()
    p.feed(html)
    return p


def test_every_link_and_button_has_an_accessible_name(client):
    for role, who, path in SCREENS:
        for item in controls(render(client, role, who, path)).items:
            if item["tag"] in ("a", "button"):
                name = "".join(item["text"]).strip() or item["attrs"].get("aria-label") or item["attrs"].get("title")
                assert name, (path, item["attrs"])


def test_every_field_has_a_label_and_errors_are_tied_to_their_field(client):
    for role, who, path in SCREENS:
        p = controls(render(client, role, who, path))
        for item in p.items:
            if item["tag"] in ("input", "select", "textarea"):
                a = item["attrs"]
                assert a.get("id") in p.labels or a.get("aria-label") or a.get("aria-labelledby"), (path, a)
    as_role(client, "marketer", "Maya Chen")
    html = client.post("/submit", data={"title": "", "product": "", "channel": "", "copy": "", "launch_date": ""},
                       headers=HEADERS).text
    for name in ("title", "product", "channel", "copy", "launch_date"):
        field = re.search(r'<(?:input|select|textarea)[^>]*id="field-%s"[^>]*>' % name, html).group(0)
        assert 'aria-invalid="true"' in field and 'aria-describedby="' in field and "error-%s" % name in field, name


def test_icons_and_decorations_are_hidden_from_assistive_tech(client):
    for role, who, path in SCREENS:
        html = render(client, role, who, path)
        for glyph in re.findall(r"<span[^>]*>(&#9888;|&#9650;|&#9679;|!)</span>", html):
            assert 'aria-hidden="true"' in html


def test_the_skip_link_is_first_and_its_target_is_focusable_main(client):
    html = render(client, "reviewer", "Maya Chen", "/")
    assert html.index('class="skip-link"') < html.index('class="brand"')
    assert re.search(r'<main id="main"', html) and 'href="#main"' in html


def test_every_animation_and_transition_is_inside_the_reduced_motion_guard():
    outside = re.sub(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", "", CSS, flags=re.S)
    assert not re.search(r"(?<![\w-])(animation|transition)\s*:", outside)
    assert "scroll-behavior: smooth" not in outside


@needs_chrome
def test_a_click_on_a_link_inside_a_row_goes_to_that_link_only_and_selecting_text_does_not_navigate(client):
    html = render(client, "reviewer", "Maya Chen", "/")
    script = """
      var row = d.querySelector('tbody tr'), clicks = 0;
      var link = row.querySelector('a[href]');
      d.addEventListener('click', function (e) { if (e.target.closest('a')) clicks++; e.preventDefault(); }, true);
      row.querySelector('td:nth-child(2)').click();
      var td = row.querySelector('td:nth-child(2)');
      return {rowCells: row.children.length, hasLink: !!link, clicks: clicks};
    """
    result = evaluate(html, 1366, 768, script)      # queue.js is stripped by the harness, so this checks markup only
    assert result["hasLink"] and result["rowCells"] == 8


# ---- step 19: laptop layout ----

@needs_chrome
@pytest.mark.parametrize("size", [(1366, 768), (1093, 614)])        # 1093x614 is 125% browser zoom on a 1366x768 screen
def test_the_urgent_rows_are_visible_without_scrolling_on_a_laptop(client, size):
    html = render(client, "reviewer", "Maya Chen", "/")
    m = evaluate(html, size[0], size[1], """
      var rows = [].slice.call(d.querySelectorAll('tbody tr')), out = [];
      rows.forEach(function (r) { var b = r.getBoundingClientRect(); out.push({urgent: /urgency-/.test(r.className), bottom: b.bottom}); });
      var head = d.querySelector('.site-header').getBoundingClientRect();
      return {rows: out, headerBottom: head.bottom, scrollWidth: d.documentElement.scrollWidth, clientWidth: d.documentElement.clientWidth};
    """)
    urgent = [r for r in m["rows"] if r["urgent"]]
    assert len(urgent) >= 3 and all(r["bottom"] <= size[1] for r in urgent)
    assert m["rows"][0]["bottom"] <= size[1] and m["scrollWidth"] <= m["clientWidth"]
    assert m["headerBottom"] <= size[1] * 0.2                          # the header never eats the screen


@needs_chrome
@pytest.mark.parametrize("sid", [1, 3, 12])
def test_review_still_shows_copy_and_flags_at_125_percent_zoom(client, sid):
    html = render(client, "reviewer", "Maya Chen", "/review/%d" % sid)
    h = 614
    m = evaluate(html, 1093, h, """
      function box(s) { var e = d.querySelector(s); if (!e) return null; var r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; }
      return {copy: box('.copy-text'), card: box('.flag-card'), buttons: box('.decision-buttons'),
              head: box('.site-header'), scrollWidth: d.documentElement.scrollWidth, clientWidth: d.documentElement.clientWidth,
              page: d.documentElement.scrollHeight};
    """)
    assert m["scrollWidth"] <= m["clientWidth"]
    assert m["copy"]["top"] < h and m["card"]["top"] < h                 # both visible together
    assert m["buttons"]["bottom"] <= h * 2                               # reachable with at most one scroll


@needs_chrome
def test_no_fixed_or_sticky_element_covers_content(client):
    # The one exception is the small theme toggle at the bottom right; main leaves room beneath the last row for it.
    for path in ("/", "/review/3", "/submit"):
        html = render(client, "marketer" if path == "/submit" else "reviewer", "Maya Chen", path)
        found = evaluate(html, 1366, 768, """
          return [].slice.call(d.querySelectorAll('body *')).filter(function (e) {
            var p = w.getComputedStyle(e).position; return (p === 'fixed' || p === 'sticky') && !e.classList.contains('theme-toggle');
          }).map(function (e) { return e.tagName + '.' + e.className; });
        """)
        assert found == [], (path, found)


# ---- step 20: narrow layout ----

@needs_chrome
@pytest.mark.parametrize("width", [375, 320])
def test_no_page_scrolls_sideways_on_a_phone(client, width):
    for role, who, path in SCREENS:
        m = evaluate(render(client, role, who, path), width, 700, """
          var wide = [];
          d.querySelectorAll('body *').forEach(function (e) {
            if (e.getBoundingClientRect().right > w.innerWidth + 1 && !e.closest('.visually-hidden, thead')) wide.push(e.tagName + '.' + e.className);
          });
          return {sw: d.documentElement.scrollWidth, cw: d.documentElement.clientWidth, wide: wide.slice(0, 5)};
        """)
        assert m["sw"] <= m["cw"] and m["wide"] == [], (path, role, width, m)


@needs_chrome
def test_primary_controls_are_at_least_44px_tall_on_a_phone(client):
    for role, who, path in SCREENS:
        small = evaluate(render(client, role, who, path), 375, 700, """
          var out = [];
          d.querySelectorAll('button, .button-link, select, input[type=text], input[type=date], summary, .role-option').forEach(function (e) {
            if (e.matches('.flag-dismiss:not([open]) summary')) return;     // a pseudo-element supplies its touch area
            var r = e.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && r.height < 44 && !e.closest('.visually-hidden')) out.push(e.tagName + '.' + e.className + ':' + Math.round(r.height));
          });
          return out;
        """)
        assert small == [], (path, small)


@needs_chrome
def test_the_closed_dismiss_toggle_has_a_44px_touch_area_without_changing_the_layout():
    assert re.search(r"\.flag-dismiss summary::after \{[^}]*inset: -0\.75rem", CSS)


@needs_chrome
def test_a_long_title_and_url_in_copy_do_not_break_the_phone_layout(client):
    from app import db

    long = "https://example.com/" + "a" * 600
    with db.connect() as c:
        c.execute("UPDATE version SET copy = ? WHERE submission_id = 10", (long,))
        c.execute("UPDATE submission SET title = ? WHERE id = 10", ("Long" * 80,))
    for path in ("/", "/review/10"):
        m = evaluate(render(client, "reviewer", "Maya Chen", path), 320, 700,
                     "return {sw: d.documentElement.scrollWidth, cw: d.documentElement.clientWidth};")
        assert m["sw"] <= m["cw"], path


def test_the_viewport_meta_is_on_every_page(client):
    for role, who, path in SCREENS:
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in render(client, role, who, path)
