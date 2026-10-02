import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db

TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")
TEXT = "Please add the APR next to the rate."


def snapshot():
    with db.connect() as c:
        return tuple(tuple(tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)) for t in TABLES)


def post(client, sid=3, text=TEXT, version="1", rule=None, headers=None, data=None):
    body = data
    if body is None:
        body = {"text": text, "version": version}
        if rule is not None:
            body["rule_id"] = rule
    return client.post(f"/review/{sid}/comment", data=body, headers=headers or {}, follow_redirects=False)


def banner(html):
    m = re.search(r'<div class="notice notice-error" role="alert"><p>(.*?)</p></div>', html, re.S)
    return m.group(1) if m else None


def field_error(html):
    m = re.search(r'<form[^>]*comment-form.*?<p class="field-error" role="alert"[^>]*>(.*?)</p>', html, re.S)
    return m.group(1) if m else None


def textarea(html):
    m = re.search(r'<textarea id="comment-text"[^>]*>\n(.*?)</textarea>', html, re.S)
    return m.group(1) if m else None


def clean(r):
    assert "Traceback" not in r.text and "sqlite" not in r.text.lower() and "/Users/" not in r.text
    assert r.headers.get("content-security-policy") and r.headers["cache-control"] == "no-store"


def test_reviewer_comments_on_3(client):
    r = post(client)
    assert r.status_code == 303 and r.headers["location"] == "/review/3#comments-heading"
    clean(r)
    page = client.get("/review/3").text
    assert TEXT in page and "Alex Rivera" in page and "About R" not in page.split("Comments (")[1].split("Decision")[0]
    assert "Comments (2)" in page  # one seeded comment plus this one


def test_linked_comment_shows_its_rule_on_12(client):
    assert post(client, 12, "Check context.", rule="R4").status_code == 303
    page = client.get("/review/12").text
    assert "Check context." in page and "About R4:" in page


def test_comment_on_an_approved_item_works_and_is_marked_post_decision(client):
    before = snapshot()
    assert post(client, 6, "Noting for the record.").status_code == 303
    after = snapshot()
    assert after[0] == before[0] and after[4] == before[4]  # status and decisions unchanged
    page = client.get("/review/6").text
    assert "Noting for the record." in page and "Post-decision" in page


def test_marketer_is_refused_and_nothing_is_written(client):
    client.cookies.set("role", "marketer")
    before = snapshot()
    r = post(client)
    assert r.status_code == 403 and snapshot() == before
    clean(r)


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "https://testserver.evil.example"])
def test_cross_origin_is_refused(client, origin):
    before = snapshot()
    assert post(client, headers={"Origin": origin}).status_code == 403
    assert post(client, headers={"Referer": origin + "/x"}).status_code == 403
    assert snapshot() == before


def test_same_origin_and_headerless_work(client):
    assert post(client, headers={"Origin": "http://testserver"}).status_code == 303


@pytest.mark.parametrize("method", ["get", "head", "options", "put", "delete", "patch"])
def test_other_methods_do_not_write(client, method):
    before = snapshot()
    assert getattr(client, method)("/review/3/comment").status_code in (404, 405)
    assert snapshot() == before


@pytest.mark.parametrize("sid", ["abc", "0", "-1", "999", "3abc", "99999999999999999999"])
def test_unknown_or_bad_id_is_404(client, sid):
    before = snapshot()
    assert post(client, sid=sid).status_code == 404 and snapshot() == before


@pytest.mark.parametrize("data", [
    {}, {"text": "x"}, {"version": "1"}, {"text": "x", "version": "abc"}, {"text": "x", "version": "-1"},
    {"text": "x", "version": "1.5"}, {"text": "x", "version": "99999999999999999999"},
    {"text": "x", "version": "1", "rule_id": ""}, {"text": "x", "version": "1", "rule_id": "R99"},
    {"text": "x", "version": "1", "rule_id": "../../etc"}, {"text": "x", "version": "99"},
])
def test_bad_fields_give_4xx_html_and_write_nothing(client, data):
    before = snapshot()
    r = post(client, data=data)
    assert r.status_code in (409, 422) and "text/html" in r.headers["content-type"]
    assert not r.text.lstrip().startswith("{") and snapshot() == before
    clean(r)


def test_json_body_is_not_a_json_error(client):
    before = snapshot()
    r = client.post("/review/3/comment", json={"text": "x", "version": "1"})
    assert r.status_code == 422 and "text/html" in r.headers["content-type"] and snapshot() == before


def test_repeated_and_file_fields_are_refused(client):
    before = snapshot()
    for data in ([("text", "a"), ("text", "b"), ("version", "1")],
                 [("text", "a"), ("version", "1"), ("rule_id", "R1"), ("rule_id", "R2")],
                 [("text", "a"), ("version", "1"), ("version", "1")]):
        assert client.post("/review/3/comment", data=data, follow_redirects=False).status_code == 422
    r = client.post("/review/3/comment", data={"version": "1"}, files={"text": ("t.txt", b"x", "text/plain")},
                    follow_redirects=False)
    assert r.status_code == 422 and snapshot() == before


@pytest.mark.parametrize("text", ["", "   ", "​"])
def test_blank_text_is_a_field_error_and_the_page_is_intact(client, text):
    before = snapshot()
    r = post(client, text=text)
    assert r.status_code == 422 and snapshot() == before
    assert field_error(r.text) == "Write a comment before posting."
    assert "Comments (1)" in r.text and banner(r.text) is None  # existing comments still shown


def test_too_long_keeps_text_and_linked_rule_and_escapes(client):
    typed = '"><script>alert(1)</script>' + "x" * 2000
    r = post(client, 12, typed, version="1", rule="R4")
    assert r.status_code == 422 and field_error(r.text) == "Keep the comment to 2,000 characters or fewer."
    assert "<script>alert(1)" not in r.text and "&lt;script&gt;alert(1)" in r.text
    assert 'name="rule_id" value="R4"' in r.text  # a failed snippet comment stays linked
    assert textarea(r.text).startswith("&#34;&gt;&lt;script&gt;")


def test_control_characters_are_refused(client):
    r = post(client, text="a\x00b")
    assert r.status_code == 422 and "control or text-direction characters" in field_error(r.text)


def test_double_submit_sequential(client):
    assert post(client).status_code == 303
    r = post(client)
    assert r.status_code == 409 and "just posted this comment" in banner(r.text)
    assert textarea(r.text) == TEXT  # nothing typed is lost


def test_concurrent_double_click_makes_one_row(client):
    with ThreadPoolExecutor(6) as pool:
        codes = sorted(f.result().status_code for f in [pool.submit(post, client) for _ in range(6)])
    assert codes == [303] + [409] * 5
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM comment WHERE text = ?", (TEXT,)).fetchone()[0] == 1


def test_stale_form_after_a_new_version(client):
    with db.connect() as c:
        c.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                  " VALUES (3, 2, 'new copy', NULL, '2026-10-02T00:00:00Z')")
        c.execute("UPDATE submission SET current_version = 2 WHERE id = 3")
    before = snapshot()
    r = post(client, version="1")
    assert r.status_code == 409 and "newer version exists" in banner(r.text) and snapshot() == before
    assert post(client, version="2").status_code == 303


def test_capacity_message(client):
    with db.connect() as c:
        have = c.execute("SELECT count(*) FROM comment WHERE submission_id = 3").fetchone()[0]
        for i in range(200 - have):
            c.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at)"
                      " VALUES (3, 1, 'x', ?, '2026-10-01T00:00:00Z')", ("c%d" % i,))
    r = post(client)
    assert r.status_code == 409 and "limit of 200 comments" in banner(r.text)


def test_huge_body_is_413(client):
    before = snapshot()
    assert post(client, text="x" * 2_000_000).status_code == 413 and snapshot() == before


@pytest.mark.parametrize("payload", ["<script>alert(1)</script>", "</textarea><b>bold</b>",
                                     "<img src=x onerror=alert(1)>"])
def test_hostile_html_is_inert_on_the_page_and_in_the_textarea(client, payload):
    assert post(client, text=payload).status_code == 303
    page = client.get("/review/3").text
    assert payload not in page and "<b>bold</b>" not in page and "<script>alert" not in page
    assert "&lt;" in page
    r = post(client, text=payload)  # the same text re-rendered after a refused post stays escaped
    assert r.status_code == 409 and payload not in r.text and "<script>alert" not in r.text


@pytest.mark.parametrize("payload", ["{{7*7}}", "${7*7}", "{% raw %}", "%(x)s"])
def test_template_syntax_is_shown_literally_and_never_evaluated(client, payload):
    assert post(client, text=payload).status_code == 303
    page = client.get("/review/3").text
    texts = re.findall(r'<p class="comment-text">(.*?)</p>', page, re.S)
    assert payload in texts and "49" not in texts


def test_author_time_and_status_come_from_the_server(client):
    data = {"text": "From the form", "version": "1", "author": "Mallory", "created_at": "1999-01-01T00:00:00Z",
            "id": "1", "status": "approved", "outcome": "approved", "submission_id": "9"}
    assert post(client, data=data).status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT * FROM comment WHERE text = 'From the form'").fetchone()
        assert (row["author"], row["submission_id"]) == ("Alex Rivera", 3) and row["created_at"] > "2026"
        assert c.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == "in_review"


def test_redirect_stays_on_site(client):
    for back in ("//evil.example", "https://evil.example", "/\\evil", "/?status=new\r\nX: y"):
        r = post(client, data={"text": "back " + back[:8], "version": "1", "back": back})
        loc = r.headers["location"]
        assert r.status_code == 303 and loc.startswith("/review/3") and "evil" not in loc and "\n" not in loc


def test_security_headers_on_every_status(client):
    for data in ({"text": "ok status", "version": "1"}, {"text": "", "version": "1"}, {"text": "x", "version": "9"}):
        r = post(client, data=data)
        assert r.headers.get("content-security-policy") and r.headers["cache-control"] == "no-store"
        assert r.headers.get("x-content-type-options") == "nosniff"
