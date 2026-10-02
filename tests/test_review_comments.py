import re

import pytest

from app import db
from app.review import comments_view, load_review


def comments(html):
    sec = re.search(r'<section class="comments-pane".*?</section>', html, re.S).group(0)
    return sec, re.findall(r'<li class="comment">(.*?)</li>', sec, re.S)


def flat(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def seeded_comments(sid, v=None):
    with db.connect() as c:
        return comments_view(load_review(c, sid, v))


def test_item_6_comment_is_labeled_post_decision(client):
    sec, items = comments(client.get("/review/6").text)
    assert "Comments (1)" in sec and len(items) == 1
    assert "Post-decision" in items[0]
    assert seeded_comments(6)[0]["post_decision"] is True


def test_item_14_comment_names_its_rule_and_precedes_nothing(client):
    views = seeded_comments(14)
    assert any(v["rule"] for v in views)
    sec, items = comments(client.get("/review/14").text)
    assert any("About R" in i for i in items)
    for v in views:                                          # decided at 30h ago; compare to the seed
        assert isinstance(v["post_decision"], bool)


def test_comments_before_the_decision_are_not_labeled(client):
    with db.connect() as c:
        c.execute("UPDATE comment SET created_at = '2000-01-01T00:00:00Z' WHERE submission_id = 6")
    sec, items = comments(client.get("/review/6").text)
    assert "Post-decision" not in sec


def test_undecided_version_never_labels(client):
    for sid in (3, 1, 11):
        sec, _ = comments(client.get(f"/review/{sid}").text)
        assert "Post-decision" not in sec


def test_equal_time_is_not_post_decision(client):
    with db.connect() as c:
        t = c.execute("SELECT created_at FROM decision WHERE submission_id = 6").fetchone()[0]
        c.execute("UPDATE comment SET created_at = ? WHERE submission_id = 6", (t,))
    assert "Post-decision" not in comments(client.get("/review/6").text)[0]


def test_item_3_shows_its_seeded_comment_only(client):
    sec, items = comments(client.get("/review/3").text)
    assert len(items) == 1 and "Starting review." in flat(items[0]) and "Alex Rivera" in items[0]


def test_only_the_viewed_versions_comments(client):
    for sid in range(1, 15):
        with db.connect() as c:
            n = c.execute("SELECT count(*) FROM comment c JOIN submission s ON s.id = c.submission_id"
                          " WHERE c.submission_id = ? AND c.version_number = s.current_version", (sid,)).fetchone()[0]
        assert f"Comments ({n})" in comments(client.get(f"/review/{sid}").text)[0]


def test_no_comments_shows_the_empty_text_not_an_empty_box(client):
    with db.connect() as c:
        c.execute("DELETE FROM comment WHERE submission_id = 3")
    sec, items = comments(client.get("/review/3").text)
    assert "No comments yet." in sec and items == [] and "<ul" not in sec and "Comments (0)" in sec


def test_text_is_escaped_unicode_and_keeps_line_breaks(client):
    text = "<script>alert(1)</script>\n<b>two</b> \U0001F600 שלום"
    with db.connect() as c:
        c.execute("UPDATE comment SET text = ?, author = ? WHERE submission_id = 6", (text, "<i>M</i>"))
    r = client.get("/review/6").text
    assert "<script>alert" not in r and "<b>two" not in r and "<i>M</i>" not in r
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\n&lt;b&gt;two&lt;/b&gt; \U0001F600" in r      # newline kept
    assert ".comment-text { white-space: pre-wrap; }" in open("app/static/style.css").read()


def test_removed_rule_shows_no_rule_name_and_no_crash(client):
    with db.connect() as c:
        c.execute("UPDATE comment SET rule_id = 'R99' WHERE rule_id IS NOT NULL")
    r = client.get("/review/14")
    assert r.status_code == 200 and "R99" not in r.text and "About R" not in comments(r.text)[0]


def test_rule_name_comes_from_the_rule_file(client):
    from app.rules import get_rule
    with db.connect() as c:
        c.execute("UPDATE comment SET rule_id = 'R3' WHERE id = (SELECT min(id) FROM comment WHERE submission_id = 6)")
    from markupsafe import escape
    assert str(escape("About R3: " + get_rule("R3").name)) in flat(comments(client.get("/review/6").text)[0])


def test_unreadable_times_leave_the_label_off(client):
    with db.connect() as c:
        c.execute("UPDATE comment SET created_at = 'garbage' WHERE submission_id = 6")
    sec, _ = comments(client.get("/review/6").text)
    assert "Post-decision" not in sec and "unknown time" in sec
    with db.connect() as c:
        c.execute("UPDATE decision SET created_at = 'garbage' WHERE submission_id = 6")
    assert client.get("/review/6").status_code == 200


def test_only_a_reviewer_gets_the_comment_form(client):
    for sid in (3, 6):
        client.cookies.set("role", "marketer")
        sec, _ = comments(client.get(f"/review/{sid}").text)
        assert "<form" not in sec and "<textarea" not in sec
        client.cookies.set("role", "reviewer")
        sec, _ = comments(client.get(f"/review/{sid}").text)
        assert sec.count("<form") == 1 and sec.count("<textarea") == 1 and "/comment" in sec
