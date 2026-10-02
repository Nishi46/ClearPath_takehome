import re

import pytest

from app import db
from app.review import day_text


def meta_between(html, start_cls, end_cls=None):
    m = re.search(r'<nav class="version-nav".*?</nav>', html, re.S)
    return m.group(0)


def selector(html):
    nav = re.search(r'<nav class="version-nav".*?</nav>', html, re.S).group(0)
    nav = re.sub(r' <span class="version-resubmitted">.*?</span>', "", nav)  # the resubmission marker has its own test
    return re.findall(r'<a href="([^"]+)"( aria-current="page")? class="version-link[^"]*">([^<]+)</a>', nav)


def lock(html):
    m = re.search(r'<div class="notice lock-banner[^"]*"[^>]*>(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None


def history(html):
    html = re.sub(r' <strong class="history-resubmitted">.*?</strong>', "", html)  # marker: own test
    return re.findall(r'<li class="history-\w+">(.*?) <span class="history-when">(.*?)</span></li>', html, re.S)


def test_item_7_selector_and_banners(client):
    html = client.get("/review/7").text
    assert selector(html) == [("/review/7?v=1", "", "v1"), ("/review/7", ' aria-current="page"', "v2 (current)")]
    assert lock(html).startswith("Locked: Approved by Alex Rivera,")
    assert "You are viewing" not in html
    old = client.get("/review/7?v=1").text
    assert lock(old).startswith("Locked: Rejected by Alex Rivera,")
    assert "Reason: The concept encourages spending" in old
    assert "You are viewing v1. The current version is v2." in old
    assert selector(old)[0][1] == ' aria-current="page"'


def test_item_5_current_has_no_lock_and_v1_shows_changes_requested(client):
    cur = client.get("/review/5").text
    assert lock(cur) is None and "You are viewing" not in cur
    old = client.get("/review/5?v=1").text
    assert "Changes requested by Alex Rivera" in lock(old)
    assert "Reason: The intro rate is shown without an APR" in old
    assert "You are viewing v1. The current version is v2." in old
    assert 'href="/review/5">Go to v2' in old


@pytest.mark.parametrize("sid, start", [
    (6, "Locked: Approved by Alex Rivera, "), (8, "Locked: Rejected by Alex Rivera, "),
    (13, "Locked: Approved by Alex Rivera, "), (14, "Changes requested by Alex Rivera, ")])
def test_decided_items_show_lock_with_reviewer_and_date(client, sid, start):
    text = lock(client.get(f"/review/{sid}").text)
    assert text.startswith(start)
    assert re.search(r"[A-Z][a-z]{2} \d{1,2}, 20\d\d\.", text)
    assert ("Locked until the marketer resubmits" in text) == (sid == 14)


@pytest.mark.parametrize("sid", [1, 2, 3, 4, 9, 10, 11, 12])
def test_undecided_items_have_no_lock(client, sid):
    assert lock(client.get(f"/review/{sid}").text) is None


def test_single_version_selector(client):
    assert selector(client.get("/review/3").text) == [("/review/3", ' aria-current="page"', "v1 (current)")]


def test_history_order_item_7_and_5(client):
    h7 = [t for t, _ in history(client.get("/review/7").text)]
    assert h7 == ["v1 submitted by Jordan Lee", "v1 rejected by Alex Rivera",
                  "v2 submitted by Jordan Lee", "v2 approved by Alex Rivera"]
    h5 = [t for t, _ in history(client.get("/review/5").text)]
    assert h5 == ["v1 submitted by Maya Chen", "v1 changes requested by Alex Rivera", "v2 submitted by Maya Chen"]
    assert all(re.match(r"[A-Z][a-z]{2} \d+, 20\d\d$", w) for _, w in history(client.get("/review/7").text))


def test_history_is_the_same_for_every_version_viewed(client):
    assert history(client.get("/review/7").text) == history(client.get("/review/7?v=1").text)


def test_reason_and_names_are_escaped(client):
    evil = "<script>alert(1)</script> <b>x</b>"
    with db.connect() as c:
        c.execute("UPDATE decision SET reason = ?, reviewer = ? WHERE submission_id = 6", (evil, "<i>M</i>"))
    html = client.get("/review/6").text
    assert "<script>alert" not in html and "<b>x</b>" not in html and "<i>M</i>" not in html
    assert "&lt;script&gt;" in html and "&lt;i&gt;M&lt;/i&gt;" in html


def test_approval_without_a_reason_shows_no_reason_line(client):
    with db.connect() as c:
        c.execute("UPDATE decision SET reason = NULL WHERE submission_id = 6")
    html = client.get("/review/6").text
    assert "Locked: Approved" in html and "Reason:" not in html


def test_long_reason_renders_and_css_wraps(client):
    with db.connect() as c:
        c.execute("UPDATE decision SET reason = ? WHERE submission_id = 6", ("w" * 2000,))
    assert "w" * 2000 in client.get("/review/6").text
    css = open("app/static/style.css").read()
    assert ".lock-reason { white-space: pre-wrap; }" in css and "overflow-wrap: anywhere" in css


def test_bad_stored_timestamps_do_not_break_the_page(client):
    with db.connect() as c:
        c.execute("UPDATE decision SET created_at = 'garbage'")
        c.execute("UPDATE version SET created_at = ''")
    for sid in range(1, 15):
        r = client.get(f"/review/{sid}")
        assert r.status_code == 200
    assert "unknown date" in client.get("/review/6").text


def test_unknown_outcome_does_not_crash(client):
    with db.connect() as c:
        c.execute("PRAGMA ignore_check_constraints = ON")
        c.execute("UPDATE decision SET outcome = '<b>x</b>' WHERE submission_id = 6")
    html = client.get("/review/6").text
    assert "<b>x</b>" not in html and "Decided by Alex Rivera" in html


def test_day_text():
    assert day_text("2026-10-02T14:05:09Z") == "Oct 2, 2026"
    assert day_text("nope") == "unknown date"
