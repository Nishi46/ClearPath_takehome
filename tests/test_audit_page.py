import re
from pathlib import Path

import pytest

from app import db
from tests.chrome_layout import CHROME, measure

needs_chrome = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")


def get(client, sid, role="reviewer", query=""):
    client.cookies.set("role", role)
    return client.get("/review/%d%s" % (sid, query)).text


def trail(html):
    m = re.search(r'<section class="audit-trail".*?</section>', html, re.S)
    return m.group(0) if m else None


def items(html):
    return re.findall(r'<li class="trail-item trail-(\w+)">(.*?)</li>', trail(html), re.S)


def plain(s):
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", " ", s))


def test_13_trail_in_order_with_the_seeded_note(client):
    got = items(get(client, 13))
    assert [k for k, _ in got] == ["version", "dismissal", "decision"]
    text = plain(got[1][1])
    assert "Flag dismissed: R1" in text and "Alex Rivera" in text and "False positive. The phrase appears" in text
    assert "Approved" in plain(got[2][1])


def test_live_dismissal_on_12_appears_in_the_trail(client):
    client.cookies.set("role", "reviewer")
    assert client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "Quoted phrase."},
                       follow_redirects=False).status_code == 303
    got = items(get(client, 12))
    assert [k for k, _ in got] == ["version", "dismissal"]
    body = plain(got[1][1])
    assert "Flag dismissed: R4" in body and "Quoted phrase." in body and "Alex Rivera" in body and "just now" in body
    assert re.search(r'<time datetime="20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ"', got[1][1])


def test_14_shows_snippet_comments_and_the_decision(client):
    from app import rules
    got = items(get(client, 14))
    comments = [plain(b) for k, b in got if k == "comment"]
    assert len(comments) == 3
    for rid in ("R2", "R3", "R7"):
        snippet = rules.describe({"rule_id": rid})["snippet"]
        assert any("(%s: " % rid in c and snippet in c for c in comments)
    decision = [plain(b) for k, b in got if k == "decision"]
    assert len(decision) == 1 and "Changes requested" in decision[0] and "Three required disclosures" in decision[0]


def test_both_roles_see_the_same_trail_and_url_options_do_not_change_it(client):
    base = items(get(client, 5))
    assert base == items(get(client, 5, role="marketer"))
    for q in ("?v=1", "?diff=1", "?back=%2F%3Fstatus%3Dnew", "?snippet=R2"):
        assert [k for k, _ in items(get(client, 5, query=q))] == [k for k, _ in base]


def test_xss_in_every_text_field_is_inert(client):
    from tests.helpers import tamper
    evil = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "</li><li>forged"]
    client.cookies.set("role", "reviewer")
    client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": evil[0]})
    client.post("/review/12/comment", data={"text": evil[1], "version": "1"})
    client.post("/review/12/decision", data={"outcome": "rejected", "version": "1", "reason": evil[2]})
    html = get(client, 12)
    t = trail(html)
    assert "<script>alert" not in t and "<img src=x" not in t and "</li><li>forged" not in t
    assert len(items(html)) == 4  # version, dismissal, comment, decision: the forged item did not become a 5th
    assert t.count("<li") == 4


def test_structure_and_heading_count(client):
    html = get(client, 13)
    t = trail(html)
    assert len(re.findall(r"<h2[ >]", t)) == 1 and "<ol" in t
    assert 'Audit trail (3)' in t and html.count('aria-labelledby="trail-heading"') == 1
    assert t.count("<time") == 3
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert 'role="region" aria-label="Audit trail entries" tabindex="0"' in t
    for k, _ in items(html):
        assert k in ("version", "dismissal", "comment", "decision")
    assert all(w in t for w in ("Version", "Dismissed", "Decision"))  # the kind is a word, not only a style


def test_a_new_submission_has_a_one_line_trail(client):
    from datetime import timedelta
    from app import clock

    client.cookies.set("role", "marketer")
    r = client.post("/submit", data={"title": "T", "product": "loan", "channel": "email",
                                     "launch_date": (clock.today() + timedelta(days=30)).isoformat(),
                                     "copy": "Hello there.", "notes": ""},
                    follow_redirects=False)
    sid = int(r.headers["location"].split("submitted=")[1])
    got = items(get(client, sid, role="marketer"))
    assert [k for k, _ in got] == ["version"] and "v1 submitted" in plain(got[0][1]) and "Maya Chen" in plain(got[0][1])


def test_trail_is_below_history_and_not_a_form(client):
    html = get(client, 13)
    assert html.index('id="history-heading"') < html.index('id="trail-heading"')
    assert "<form" not in trail(html)


@needs_chrome
@pytest.mark.parametrize("sid", [3, 12])
def test_trail_keeps_flags_and_decision_on_screen_at_1366(client, sid):
    client.cookies.set("role", "reviewer")
    html = client.get("/review/%d" % sid).text
    m = measure(html, 1366, 768)
    assert m[".copy-text"]["top"] < 768 * 0.45
    assert m[".flag-card"]["top"] < 768 and m[".flag-card"]["bottom"] <= m[".decision-pane"]["top"] + 1
    assert m["scrollWidth"] <= m["clientWidth"]


@needs_chrome
def test_no_horizontal_scroll_at_375_with_a_long_trail(client):
    from app import review
    client.cookies.set("role", "reviewer")
    client.post("/review/3/comment", data={"text": "Unbroken" * 250, "version": "1"})
    m = measure(client.get("/review/3").text, 375, 800)
    assert m["scrollWidth"] <= m["clientWidth"]
