import html as htmllib
import re
from pathlib import Path

import pytest

from app import db
from tests.helpers import tamper


def main(html):
    return html.split("<main", 1)[1]


def back_link(html):
    m = re.search(r'<p class="review-back"><a href="([^"]*)">&larr; ([^<]*)</a></p>', html)
    return m.group(1), m.group(2)


def feedback(html):
    m = re.search(r'<section class="marketer-feedback".*?</section>', html, re.S)
    return m.group(0) if m else None


def plain(fragment):
    return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


@pytest.fixture
def jordan(mclient):
    mclient.post("/marketer", data={"name": "Jordan Lee"})
    return mclient


# ---- the feedback block ----

def test_item_14_shows_the_reason_and_comments_to_its_marketer(jordan):
    html = jordan.get("/review/14").text
    block = feedback(html)
    with db.connect() as c:
        reason = c.execute("SELECT reason FROM decision WHERE submission_id = 14").fetchone()[0]
        comments = [r[0] for r in c.execute("SELECT text FROM comment WHERE submission_id = 14 ORDER BY id")]
    assert "Reviewer feedback on v1" in block and "Changes requested" in block and "Alex Rivera" in block
    assert reason in plain(block) and comments
    for text in comments:
        assert text in plain(block)


def test_the_reason_is_shown_once_to_a_marketer_and_in_the_banner_for_a_reviewer(client, jordan):
    with db.connect() as c:
        reason = c.execute("SELECT reason FROM decision WHERE submission_id = 14").fetchone()[0]
    # Above the audit trail, which repeats every reason on purpose.
    assert jordan.get("/review/14").text.split('class="audit-trail"')[0].count(reason) == 1
    client.cookies.set("role", "reviewer")
    assert 'class="lock-reason"' in client.get("/review/14").text


def test_it_sits_above_the_copy(jordan):
    html = jordan.get("/review/14").text
    assert html.index('id="feedback-heading"') < html.index('id="copy-heading"')


def test_comments_show_their_rule_and_the_post_decision_label(mclient):
    block = feedback(mclient.get("/review/6").text)            # #6 has a comment dated after its decision
    assert "Post-decision" in block and "Approved" in block
    with db.connect() as c:
        tamper(c, "UPDATE comment SET rule_id = 'R1' WHERE submission_id = 6")
    assert "About R1: " in feedback(mclient.get("/review/6").text)


def test_an_undecided_item_says_so_and_a_comment_free_one_says_that(mclient):
    block = feedback(mclient.get("/review/1").text)
    assert "No decision yet. A reviewer will look at this soon." in block and "No comments yet." in block


def test_item_5_shows_the_decision_of_the_version_being_viewed(mclient):
    current = feedback(mclient.get("/review/5").text)
    assert "Reviewer feedback on v2" in current and "No decision yet" in current
    old = feedback(mclient.get("/review/5?v=1").text)
    assert "Reviewer feedback on v1" in old and "Changes requested" in old and "No decision yet" not in old


def test_hostile_reasons_and_comments_are_escaped(mclient):
    evil = "<script>alert(1)</script><img src=x onerror=alert(1)>"
    with db.connect() as c:
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 6", (evil,))
        tamper(c, "UPDATE comment SET text = ?, author = ? WHERE submission_id = 6", (evil, evil))
    html = mclient.get("/review/6").text
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "&lt;script&gt;" in feedback(html)


def test_line_breaks_in_a_reason_survive(jordan):
    with db.connect() as c:
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 14", ("Line one\nLine two",))
    assert "Line one\nLine two" in feedback(jordan.get("/review/14").text)
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "style.css").read_text()
    assert re.search(r"\.feedback-reason[^{]*{[^}]*white-space:\s*pre-wrap", css)


def test_a_marketer_cannot_decide_comment_or_dismiss_from_this_page(jordan):
    html = main(jordan.get("/review/14").text)
    assert not re.search(r'<form[^>]*action="[^"]*(decision|dismiss|comment)', html)
    assert "<textarea" not in html and 'name="outcome"' not in html
    assert jordan.post("/review/14/decision", data={"outcome": "approved", "version": "1"}).status_code == 403


def test_a_marketer_sees_no_decision_form_even_on_an_open_item(mclient):
    html = main(mclient.get("/review/3").text)
    assert "Only reviewers can decide." in html and 'name="outcome"' not in html and "<textarea" not in html


def test_the_reviewer_page_has_no_marketer_block(client):
    client.cookies.set("role", "reviewer")
    html = client.get("/review/14").text
    assert feedback(html) is None and "feedback-heading" not in html
    assert back_link(html) == ("/", "Back to the queue")


# ---- the back link ----

def test_a_marketer_goes_back_to_my_submissions_by_default(mclient):
    assert back_link(mclient.get("/review/14").text) == ("/mine", "Back to my submissions")


def test_back_mine_is_kept_and_carried_through_version_links(mclient):
    html = mclient.get("/review/5?back=/mine").text
    assert back_link(html) == ("/mine", "Back to my submissions")
    nav = re.search(r'<nav class="version-nav".*?</nav>', html, re.S).group(0)
    assert nav.count("back=%2Fmine") == 2
    old = mclient.get("/review/5?v=1&back=/mine").text
    assert 'href="/review/5?back=%2Fmine"' in old            # "Go to v2" keeps the way back


def test_a_reviewer_may_also_follow_back_to_mine(client):
    client.cookies.set("role", "reviewer")
    assert back_link(client.get("/review/14?back=/mine").text) == ("/mine", "Back to my submissions")


EVIL = ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)", "/mine?x=<script>",
        "/mine/", "/MINE", "/mine ", " /mine", "/mine#x", "/minefoo", "/mine%00", "x" * 5000, "", "/?status=<script>",
        "/mine\r\nSet-Cookie: a=b", "//mine"]


@pytest.mark.parametrize("bad", EVIL)
def test_bad_back_values_fall_back_for_a_marketer(mclient, bad):
    r = mclient.get("/review/14", params={"back": bad})
    assert r.status_code == 200
    assert back_link(r.text)[0] == "/mine"
    assert "evil.example" not in r.text and "<script>" not in r.text and "javascript:" not in r.text


@pytest.mark.parametrize("bad", EVIL)
def test_bad_back_values_fall_back_for_a_reviewer(client, bad):
    client.cookies.set("role", "reviewer")
    r = client.get("/review/14", params={"back": bad})
    assert r.status_code == 200 and back_link(r.text) == ("/", "Back to the queue")
    assert "evil.example" not in r.text and "<script>" not in r.text and "javascript:" not in r.text


def test_repeated_back_values_fall_back(mclient):
    r = mclient.get("/review/14?back=/mine&back=//evil.example")
    assert back_link(r.text)[0] == "/mine" and "evil.example" not in r.text


def test_a_marketer_who_came_from_a_filtered_queue_goes_back_there(mclient):
    href, text = back_link(mclient.get("/review/3", params={"back": "/?status=in_review&product=loan"}).text)
    assert href == "/?status=in_review&amp;product=loan" or href == "/?status=in_review&product=loan"
    assert text == "Back to the queue"


def test_the_queue_still_links_with_its_filters(client):
    html = client.get("/?status=new").text
    assert 'href="/review/1?back=' in html


# ---- layout (real browser) ----

@pytest.mark.skipif(not Path("/Applications/Google Chrome.app").exists(), reason="Google Chrome is not installed here")
@pytest.mark.parametrize("size", [(1366, 768), (390, 844)])
def test_long_feedback_does_not_widen_the_page(jordan, size):
    from tests.chrome_layout import measure
    with db.connect() as c:
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 14", ("Unbroken" * 625,))
        tamper(c, "UPDATE comment SET text = ? WHERE submission_id = 14", ("Comment" * 700,))
    m = measure(jordan.get("/review/14").text, *size)
    assert m["scrollWidth"] <= m["clientWidth"] + 1 and m["wide"] == [], m["wide"]
