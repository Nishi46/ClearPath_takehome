import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db

TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")
NOTE = "False positive: quoted phrase."


def snapshot():
    with db.connect() as c:
        return tuple(tuple(tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)) for t in TABLES)


def post(client, sid=12, rule="R4", version="1", note=NOTE, headers=None, data=None):
    body = data if data is not None else {"rule_id": rule, "version": version, "note": note}
    return client.post(f"/review/{sid}/dismiss", data=body, headers=headers or {}, follow_redirects=False)


def banner(html):
    m = re.search(r'<div class="notice notice-error" role="alert"><p>(.*?)</p></div>', html, re.S)
    return m.group(1) if m else None


def field_error(html):
    m = re.search(r'<p class="field-error" role="alert"[^>]*>(.*?)</p>', html, re.S)
    return m.group(1) if m else None


def assert_clean(r):
    assert "Traceback" not in r.text and "sqlite" not in r.text.lower() and "/Users/" not in r.text
    assert r.headers.get("content-security-policy")
    assert r.headers["cache-control"] == "no-store"


def test_reviewer_dismisses_12_r4(client):
    page = client.get("/review/12").text
    assert 'id="flag-' in page and "R4:" in page
    r = post(client)
    assert r.status_code == 303 and r.headers["location"] == "/review/12#flags-heading"
    assert_clean(r)
    page = client.get("/review/12").text
    assert "Dismissed (1)" in page and "Note: " + NOTE in page and "Alex Rivera" in page
    assert "Flags (0)" in page and 'id="flag-' not in page  # no open card, no highlight
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 2


def test_the_queue_row_shows_no_flags_after(client):
    from app.queue import list_queue

    def count():
        with db.connect() as c:
            return next(dict(r) for r in list_queue(c) if r["id"] == 12)

    assert (count()["flag_count"], count()["top_severity"]) == (1, "medium")
    post(client)
    assert (count()["flag_count"], count()["top_severity"]) == (0, None)


@pytest.mark.parametrize("role", ["marketer"])
def test_marketer_is_refused_and_nothing_is_written(client, role):
    before = snapshot()
    client.cookies.set("role", role)
    r = post(client)
    assert r.status_code == 403 and snapshot() == before
    assert_clean(r)


def test_forged_role_falls_back_to_reviewer_as_documented(client):
    client.cookies.set("role", "admin")
    assert post(client).status_code == 303


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "https://testserver.evil.example"])
def test_cross_origin_is_refused(client, origin):
    before = snapshot()
    r = post(client, headers={"Origin": origin})
    assert r.status_code == 403 and snapshot() == before
    r = post(client, headers={"Referer": origin + "/x"})
    assert r.status_code == 403 and snapshot() == before


def test_same_origin_and_headerless_work(client):
    assert post(client, headers={"Origin": "http://testserver"}).status_code == 303


@pytest.mark.parametrize("method", ["get", "head", "options", "put", "delete", "patch"])
def test_other_methods_do_not_write(client, method):
    before = snapshot()
    r = getattr(client, method)("/review/12/dismiss")
    assert r.status_code in (404, 405) and snapshot() == before


@pytest.mark.parametrize("sid", ["abc", "0", "-1", "999", "12abc", "１２", "99999999999999999999"])
def test_unknown_or_bad_id_is_404(client, sid):
    before = snapshot()
    assert post(client, sid=sid).status_code == 404 and snapshot() == before


@pytest.mark.parametrize("data", [
    {}, {"rule_id": "R4"}, {"version": "1", "note": "x"}, {"rule_id": "R4", "note": "x"},
    {"rule_id": "R4", "version": "abc", "note": "x"}, {"rule_id": "R4", "version": "-1", "note": "x"},
    {"rule_id": "R4", "version": "1.5", "note": "x"}, {"rule_id": "R4", "version": "99999999999999999999", "note": "x"},
    {"rule_id": "../../etc", "version": "1", "note": "x"}, {"rule_id": "R2", "version": "1", "note": "x"},
    {"rule_id": "R4", "version": "99", "note": "x"},
])
def test_bad_fields_give_4xx_html_and_write_nothing(client, data):
    before = snapshot()
    r = post(client, data=data)
    assert r.status_code in (409, 422) and "text/html" in r.headers["content-type"]
    assert snapshot() == before
    assert not r.text.lstrip().startswith("{")  # never FastAPI's JSON error shape
    assert_clean(r)


def test_json_body_is_not_a_json_error(client):
    before = snapshot()
    r = client.post("/review/12/dismiss", json={"rule_id": "R4", "version": "1", "note": "x"})
    assert r.status_code == 422 and "text/html" in r.headers["content-type"] and snapshot() == before


def test_repeated_fields_and_file_note_are_refused(client):
    before = snapshot()
    r = client.post("/review/12/dismiss", data=[("rule_id", "R4"), ("rule_id", "R4"), ("version", "1"),
                                                ("note", "x")], follow_redirects=False)
    assert r.status_code == 422
    r = client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1"},
                    files={"note": ("n.txt", b"x", "text/plain")}, follow_redirects=False)
    assert r.status_code == 422
    r = client.post("/review/12/dismiss", data=[("rule_id", "R4"), ("version", "1"), ("note", "a"),
                                                ("note", "b")], follow_redirects=False)
    assert r.status_code == 422 and snapshot() == before


@pytest.mark.parametrize("note", ["", "   ", "\t\n", "​"])
def test_blank_note_is_a_field_error_and_page_stays_intact(client, note):
    before = snapshot()
    r = post(client, note=note)
    assert r.status_code == 422 and snapshot() == before
    assert field_error(r.text) == "Add a note saying why this flag doesn&#39;t apply."
    assert 'details class="flag-dismiss" open' in r.text and "R4:" in r.text
    assert banner(r.text) is None


def test_note_too_long_keeps_and_escapes_the_typed_text(client):
    typed = '"><script>alert(1)</script>' + "x" * 1000
    r = post(client, note=typed)
    assert r.status_code == 422
    assert field_error(r.text) == "Keep the note to 1,000 characters or fewer."
    assert "<script>alert(1)" not in r.text and "&lt;script&gt;alert(1)" in r.text
    assert re.search(r"<textarea[^>]*>\n&#34;&gt;&lt;script&gt;", r.text)


def test_control_characters_are_refused(client):
    r = post(client, note="a\x00b")
    assert r.status_code == 422 and "control characters" in field_error(r.text)


def test_double_submit_sequential_and_concurrent(client):
    assert post(client).status_code == 303
    r = post(client)
    assert r.status_code == 409
    assert "R4 was already dismissed by Alex Rivera" in banner(r.text) and "not saved" in banner(r.text)


def test_concurrent_double_submit_makes_one_row(client):
    with ThreadPoolExecutor(6) as pool:
        codes = sorted(f.result().status_code for f in [pool.submit(post, client) for _ in range(6)])
    assert codes == [303] + [409] * 5
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM flag_dismissal WHERE rule_id = 'R4'").fetchone()[0] == 1


def test_stale_form_after_a_new_version(client):
    with db.connect() as c:
        c.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                  " VALUES (12, 2, 'new copy', NULL, '2026-10-02T00:00:00Z')")
        c.execute("UPDATE submission SET current_version = 2 WHERE id = 12")
    before = snapshot()
    r = post(client, version="1")
    assert r.status_code == 409 and "newer version exists" in banner(r.text)
    assert snapshot() == before


@pytest.mark.parametrize("sid, rule", [(13, "R1"), (14, "R2"), (6, "R1"), (8, "R1")])
def test_decided_items_are_refused(client, sid, rule):
    before = snapshot()
    r = post(client, sid=sid, rule=rule)
    assert r.status_code == 409 and "already" in banner(r.text) and "Your dismissal was not saved" in banner(r.text)
    assert snapshot() == before


def test_decision_in_another_tab_wins(client):
    client.post("/review/12/decision", data={"outcome": "approved", "version": "1"})
    before = snapshot()
    r = post(client)
    assert r.status_code == 409 and "already approved by Alex Rivera" in banner(r.text)
    assert snapshot() == before


def test_huge_body_is_413(client):
    before = snapshot()
    r = post(client, note="x" * 2_000_000)
    assert r.status_code == 413 and snapshot() == before


@pytest.mark.parametrize("back", ["//evil.example", "https://evil.example", "/\\evil", "/?status=new\r\nX: y",
                                  "/mine", "/?status=new&evil=1"])
def test_redirect_stays_on_site(client, back):
    r = post(client, data={"rule_id": "R4", "version": "1", "note": "x", "back": back})
    loc = r.headers["location"]
    assert r.status_code == 303 and loc.startswith("/review/12") and "\r" not in loc and "\n" not in loc
    assert "evil" not in loc


@pytest.mark.parametrize("status_case", [("note", ""), ("version", "9")])
def test_every_response_has_security_headers(client, status_case):
    data = {"rule_id": "R4", "version": "1", "note": "x"}
    data[status_case[0]] = status_case[1]
    r = post(client, data=data)
    assert r.headers.get("content-security-policy") and r.headers["cache-control"] == "no-store"
    assert r.headers.get("x-content-type-options") == "nosniff"
