import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db
from tests.helpers import tamper


def snapshot():
    with db.connect() as c:
        return ([tuple(r) for r in c.execute("SELECT id, status FROM submission ORDER BY id")],
                [tuple(r) for r in c.execute("SELECT * FROM decision ORDER BY id")])


def post(client, sid=3, outcome="approved", version="1", reason=None, headers=None, data=None):
    body = data if data is not None else {"outcome": outcome, "version": version}
    if data is None and reason is not None:
        body["reason"] = reason
    return client.post(f"/review/{sid}/decision", data=body, headers=headers or {}, follow_redirects=False)


def banner(html):
    m = re.search(r'<div class="notice notice-error" role="alert"><p>(.*?)</p></div>', html, re.S)
    return m.group(1) if m else None


# ---- success ----

def test_approve_3_redirects_and_locks(client):
    r = post(client)
    assert r.status_code == 303 and r.headers["location"] == "/review/3"
    assert r.headers["cache-control"] == "no-store"
    page = client.get("/review/3").text
    assert "Locked: Approved by Alex Rivera" in page
    assert re.search(r'Approved\s*</', client.get("/").text) and "approved" in str(snapshot()[0][2])


@pytest.mark.parametrize("outcome", ["changes_requested", "rejected"])
def test_negative_outcomes_with_a_reason(client, outcome):
    assert post(client, outcome=outcome, reason="Add the APR.").status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT outcome, reason, reviewer FROM decision WHERE submission_id = 3").fetchone()
    assert tuple(row) == (outcome, "Add the APR.", "Alex Rivera")


def test_reviewer_and_time_come_from_the_server(client):
    post(client, data={"outcome": "approved", "version": "1", "reviewer": "Mallory", "created_at": "1999-01-01T00:00:00Z",
                       "status": "rejected", "submission_id": "9"})
    with db.connect() as c:
        row = c.execute("SELECT reviewer, created_at FROM decision WHERE submission_id = 3").fetchone()
        status = c.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0]
    assert row["reviewer"] == "Alex Rivera" and row["created_at"] > "2020" and status == "approved"


def test_status_only_changes_through_the_outcome(client):
    post(client, outcome="rejected", reason="r", data={"outcome": "rejected", "version": "1", "reason": "r", "status": "approved"})
    assert dict(snapshot()[0])[3] == "rejected"


# ---- role and origin ----

def test_marketer_is_refused_and_nothing_is_written(client):
    before = snapshot()
    client.cookies.set("role", "marketer")
    r = post(client)
    assert r.status_code == 403 and "Only reviewers" in r.text and r.headers["cache-control"] == "no-store"
    assert snapshot() == before


@pytest.mark.parametrize("cookie", [None, "admin", "", "REVIEWER"])
def test_missing_or_forged_role_falls_back_to_reviewer(client, cookie):
    # Documented: with no valid cookie the default role is reviewer (roles.get_role).
    if cookie is not None:
        client.cookies.set("role", cookie)
    assert post(client).status_code == 303


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://testserver.evil.example"])
def test_cross_origin_is_refused(client, origin):
    before = snapshot()
    assert post(client, headers={"Origin": origin}).status_code == 403
    assert post(client, headers={"Referer": origin + "/x"}).status_code == 403
    assert snapshot() == before


def test_same_origin_and_headerless_work(client):
    assert post(client, sid=1, headers={"Origin": "http://testserver"}).status_code == 303
    assert post(client, sid=2).status_code == 303


# ---- ids ----

@pytest.mark.parametrize("sid", ["9999", "abc", "0", "-1", "1.5", "%E2%80%AE3", "1" * 12])
def test_unknown_or_bad_id_is_404_and_writes_nothing(client, sid):
    before = snapshot()
    r = client.post(f"/review/{sid}/decision", data={"outcome": "approved", "version": "1"})
    assert r.status_code == 404 and "Page not found" in r.text
    assert snapshot() == before


@pytest.mark.parametrize("method", ["get", "put", "delete", "patch"])
def test_other_methods_do_not_write(client, method):
    before = snapshot()
    assert getattr(client, method)("/review/3/decision").status_code in (404, 405)
    assert client.head("/review/3/decision").status_code in (404, 405)
    assert client.options("/review/3/decision").status_code in (200, 404, 405)
    assert snapshot() == before


# ---- malformed forms ----

@pytest.mark.parametrize("data", [
    {}, {"outcome": "approved"}, {"version": "1"}, {"outcome": "approved", "version": ""},
    {"outcome": "approved", "version": "abc"}, {"outcome": "approved", "version": "-1"},
    {"outcome": "approved", "version": "1.5"}, {"outcome": "approved", "version": "9" * 30},
    {"outcome": "approved", "version": "٣"},
])
def test_missing_or_bad_fields_give_422_and_the_html_page(client, data):
    before = snapshot()
    r = post(client, data=data)
    assert r.status_code == 422 and r.headers["content-type"].startswith("text/html")
    assert '"detail"' not in r.text and "Traceback" not in r.text
    assert banner(r.text) and snapshot() == before


def test_json_body_is_not_a_json_error(client):
    before = snapshot()
    r = client.post("/review/3/decision", json={"outcome": "approved", "version": "1"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("text/html")
    assert snapshot() == before


def test_repeated_fields_are_refused(client):
    before = snapshot()
    r = client.post("/review/3/decision", data=[("outcome", "approved"), ("outcome", "rejected"), ("version", "1")])
    assert r.status_code == 422
    r = client.post("/review/3/decision", data=[("outcome", "approved"), ("version", "1"), ("version", "1")])
    assert r.status_code == 422
    assert snapshot() == before


def test_unknown_outcome_value(client):
    for value in ("APPROVED", "approve", "in_review", "", "approved' OR '1'='1"):
        r = post(client, outcome=value)
        assert r.status_code == 422 and banner(r.text) == "Choose Approve, Request changes or Reject."
    assert snapshot()[1] == snapshot()[1] and dict(snapshot()[0])[3] == "in_review"


# ---- reasons ----

@pytest.mark.parametrize("reason", [None, "", "   ", "\t\n", "​"])
def test_request_changes_needs_a_reason(client, reason):
    before = snapshot()
    r = post(client, outcome="changes_requested", reason=reason)
    assert r.status_code == 422 and banner(r.text) == "A reason is required to request changes or reject."
    assert snapshot() == before
    assert post(client, outcome="rejected", reason=reason).status_code == 422


def test_reason_too_long(client):
    before = snapshot()
    r = post(client, outcome="rejected", reason="x" * 2001)
    assert r.status_code == 422 and banner(r.text) == "Keep the reason under 2000 characters."
    assert snapshot() == before
    assert post(client, outcome="rejected", reason="x" * 2000).status_code == 303


def test_huge_body_is_413_and_writes_nothing(client):
    before = snapshot()
    r = client.post("/review/3/decision", data={"outcome": "rejected", "version": "1", "reason": "x" * (2 * 1024 * 1024)})
    assert r.status_code == 413 and snapshot() == before


# ---- conflicts ----

def test_double_submit_sequential(client):
    assert post(client).status_code == 303
    r = post(client)
    assert r.status_code == 409
    assert "already approved by Alex Rivera" in banner(r.text) and "Your decision was not saved" in banner(r.text)
    assert len(snapshot()[1]) == 8


def test_double_submit_concurrent(client):
    with ThreadPoolExecutor(6) as pool:
        codes = sorted(f.result().status_code for f in [pool.submit(post, client, 3) for _ in range(6)])
    assert codes == [303] + [409] * 5
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM decision WHERE submission_id = 3").fetchone()[0] == 1


def test_two_tabs_the_first_decision_stands(client):
    assert post(client, outcome="rejected", reason="No.").status_code == 303
    r = post(client, outcome="approved")
    assert r.status_code == 409 and "already rejected" in banner(r.text)
    assert dict(snapshot()[0])[3] == "rejected"


def test_stale_form_after_a_new_version(client):
    with db.connect() as c:
        c.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                  " VALUES (3, 2, 'new copy', NULL, '2026-10-02T00:00:00Z')")
        c.execute("UPDATE submission SET current_version = 2 WHERE id = 3")
    before = snapshot()
    r = post(client, version="1")
    assert r.status_code == 409 and "newer version exists" in banner(r.text)
    assert snapshot() == before
    assert post(client, version="2").status_code == 303


@pytest.mark.parametrize("sid, v", [(6, "1"), (7, "2"), (8, "1"), (13, "1"), (14, "1"), (7, "1"), (5, "1")])
@pytest.mark.parametrize("outcome", ["approved", "changes_requested", "rejected"])
def test_locked_and_old_versions_are_refused_server_side(client, sid, v, outcome):
    before = snapshot()
    r = post(client, sid=sid, version=v, outcome=outcome, reason="r")
    assert r.status_code == 409 and "Your decision was not saved" in banner(r.text)
    assert snapshot() == before


def test_locked_status_without_a_decision_row(client):
    with db.connect() as c:
        c.execute("UPDATE submission SET status = 'approved' WHERE id = 3")
    r = post(client)
    assert r.status_code == 409 and banner(r.text) == "This version is locked. Your decision was not saved."


# ---- output and headers ----

def test_conflict_banner_is_escaped(client):
    post(client, sid=3)
    with db.connect() as c:
        tamper(c, "UPDATE decision SET reviewer = ? WHERE submission_id = 3", ("<script>alert(1)</script>",))
    r = post(client, sid=3)
    assert "<script>alert" not in r.text and "&lt;script&gt;" in banner(r.text)


@pytest.mark.parametrize("kind", ["ok", "cross", "role", "bad", "conflict"])
def test_security_headers_on_every_outcome(client, kind):
    if kind == "ok":
        r = post(client)
    elif kind == "cross":
        r = post(client, headers={"Origin": "https://evil.example"})
    elif kind == "role":
        client.cookies.set("role", "marketer")
        r = post(client)
    elif kind == "bad":
        r = post(client, data={})
    else:
        r = post(client, sid=6)
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "x-content-type-options" in r.headers


def test_no_error_leaks_internals(client):
    for r in (post(client, data={}), post(client, sid=6), post(client, sid=9999), post(client, outcome="x")):
        for needle in ("Traceback", "sqlite", "SELECT ", "/Users/", "app/review.py", "IntegrityError"):
            assert needle not in r.text


def test_sql_in_fields_is_inert(client):
    r = post(client, outcome="rejected", reason="'); DROP TABLE decision;--")
    assert r.status_code == 303 and len(snapshot()[1]) == 8
