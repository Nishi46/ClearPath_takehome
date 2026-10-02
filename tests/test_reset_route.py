import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import clock, seed
from app.cooldown import Cooldown
from app.routes import pages
from app.security import CSP, MAX_BODY_BYTES

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
GOOD = {"confirm": "reset"}


@pytest.fixture
def live(db_path):
    """A client that ran startup (so the DB is seeded) and returns 500s instead of raising."""
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def q(db_path, sql, args=()):
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def mutate(db_path):
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE submission SET status = 'approved', title = 'Changed' WHERE id = 1")
    c.execute("DELETE FROM comment")
    c.commit()
    c.close()


def is_seed_state(db_path):
    return (q(db_path, "SELECT title, status FROM submission WHERE id = 1") == [("Personal loan holiday email", "new")]
            and q(db_path, "SELECT count(*) FROM comment") == [(14,)])


def assert_secure(r):
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"


# ---- methods ----

@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_only_post_is_allowed_on_reset(live, db_path, method):
    mutate(db_path)
    assert live.request(method, "/reset").status_code == 405
    assert not is_seed_state(db_path)  # nothing happened


def test_confirm_page_is_get_only(live):
    assert live.get("/reset/confirm").status_code == 200
    assert live.post("/reset/confirm").status_code == 405


# ---- confirmation field ----

@pytest.mark.parametrize("data", [None, {}, {"confirm": ""}, {"confirm": "no"}, {"confirm": "RESET"},
                                  {"confirm": "reset "}, {"confirm": "yes"}, {"other": "reset"}])
def test_missing_or_wrong_confirmation_is_400_and_changes_nothing(live, db_path, data):
    mutate(db_path)
    r = live.post("/reset", data=data)
    assert r.status_code == 400
    assert not is_seed_state(db_path)
    assert "no-store" in r.headers["cache-control"]


def test_json_body_is_not_a_confirmation(live, db_path):
    mutate(db_path)
    assert live.post("/reset", json={"confirm": "reset"}).status_code == 400
    assert not is_seed_state(db_path)


def test_confirmation_in_the_query_string_is_ignored(live, db_path):
    mutate(db_path)
    assert live.post("/reset?confirm=reset").status_code == 400
    assert not is_seed_state(db_path)


def test_rejected_attempts_do_not_start_the_cooldown(live, db_path):
    assert live.post("/reset", data={"confirm": "no"}).status_code == 400
    assert live.post("/reset", data=GOOD, headers={"Origin": "https://evil.example"}).status_code == 403
    mutate(db_path)
    assert live.post("/reset", data=GOOD, follow_redirects=False).status_code == 303
    assert is_seed_state(db_path)


# ---- the happy path ----

def test_valid_post_resets_and_redirects(live, db_path):
    mutate(db_path)
    assert not is_seed_state(db_path)
    r = live.post("/reset", data=GOOD, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?reset=done"
    assert "no-store" in r.headers["cache-control"]
    assert is_seed_state(db_path)


def test_following_the_redirect_lands_on_the_queue(live, db_path):
    r = live.post("/reset", data=GOOD)
    assert r.status_code == 200 and str(r.url).endswith("/?reset=done")


def test_either_role_may_reset(live, db_path):
    live.cookies.set("role", "marketer")
    mutate(db_path)
    assert live.post("/reset", data=GOOD, follow_redirects=False).status_code == 303
    assert is_seed_state(db_path)


# ---- cross-site ----

@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://testserver:8000"},               # different port
    {"Origin": "http://testserver.evil.example"},       # look-alike suffix
    {"Origin": "http://testserver@evil.example"},       # userinfo trick
    {"Origin": ""},
    {"Referer": "https://evil.example/page"},
    {"Referer": "http://evil.example/http://testserver/"},
])
def test_cross_site_posts_are_403_and_change_nothing(live, db_path, headers):
    mutate(db_path)
    r = live.post("/reset", data=GOOD, headers=headers)
    assert r.status_code == 403
    assert not is_seed_state(db_path)
    assert "evil" not in r.text  # nothing is echoed back


@pytest.mark.parametrize("headers", [
    {},                                                  # curl, no Origin
    {"Origin": "http://testserver"},
    {"Referer": "http://testserver/reset/confirm"},
])
def test_same_origin_and_headerless_posts_are_accepted(live, db_path, headers):
    mutate(db_path)
    assert live.post("/reset", data=GOOD, headers=headers, follow_redirects=False).status_code == 303
    assert is_seed_state(db_path)


def test_bad_origin_is_checked_before_confirmation(live):
    assert live.post("/reset", headers={"Origin": "https://evil.example"}).status_code == 403


# ---- cooldown ----

@pytest.fixture
def frozen(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(clock, "now", lambda: state["now"])
    return state


def test_second_reset_inside_the_cooldown_is_429_and_does_not_run(live, db_path, frozen):
    assert live.post("/reset", data=GOOD, follow_redirects=False).status_code == 303
    mutate(db_path)
    frozen["now"] = T0 + timedelta(seconds=3)
    r = live.post("/reset", data=GOOD, follow_redirects=False)
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 10
    assert "no-store" in r.headers["cache-control"]
    assert not is_seed_state(db_path)  # the second reset did not run


def test_reset_works_again_after_the_cooldown(live, db_path, frozen):
    live.post("/reset", data=GOOD)
    mutate(db_path)
    frozen["now"] = T0 + timedelta(seconds=9.9)
    assert live.post("/reset", data=GOOD, follow_redirects=False).status_code == 429
    frozen["now"] = T0 + timedelta(seconds=10.1)
    assert live.post("/reset", data=GOOD, follow_redirects=False).status_code == 303
    assert is_seed_state(db_path)


def test_double_click_runs_one_reset_and_leaves_the_seed_state(live, db_path):
    mutate(db_path)
    results = []
    gate = threading.Barrier(2)

    def click():
        gate.wait()
        results.append(live.post("/reset", data=GOOD, follow_redirects=False).status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [303, 429]
    assert is_seed_state(db_path)
    assert q(db_path, "SELECT count(*) FROM submission") == [(14,)]
    assert live.get("/").status_code == 200  # the app is still fine


def test_cooldown_unit():
    c = Cooldown(10)
    assert c.acquire(T0) == 0.0
    assert c.acquire(T0 + timedelta(seconds=4)) == pytest.approx(6.0)
    assert c.acquire(T0 + timedelta(seconds=10)) == 0.0   # boundary: exactly 10s is allowed
    c.release()                                            # back to the acquire at T0
    assert c.acquire(T0 + timedelta(seconds=5)) == pytest.approx(5.0)
    c.clear()
    assert c.acquire(T0) == 0.0


# ---- size and failure ----

def test_oversized_body_is_413(live, db_path):
    mutate(db_path)
    r = live.post("/reset", content=b"confirm=reset&x=" + b"a" * (10 * 1024 * 1024),
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert len(b"a" * 1) and MAX_BODY_BYTES < 10 * 1024 * 1024
    assert not is_seed_state(db_path)


def test_a_failing_reset_is_a_friendly_500_and_changes_nothing(live, db_path, monkeypatch):
    mutate(db_path)

    def boom(conn, now=None):
        conn.execute("DELETE FROM comment")  # partial work that must be rolled back
        raise RuntimeError("secret-internal-detail /Users/someone/app/db.py")

    monkeypatch.setattr(seed, "reset_to_seed", boom)
    r = live.post("/reset", data=GOOD)
    assert r.status_code == 500
    assert "We hit a problem" in r.text and "/" in r.text
    for leak in ("secret-internal-detail", "Traceback", "RuntimeError", "/Users/"):
        assert leak not in r.text
    assert_secure(r)
    assert q(db_path, "SELECT title FROM submission WHERE id = 1") == [("Changed",)]


def test_a_failed_reset_does_not_lock_out_a_retry(live, db_path, monkeypatch):
    mutate(db_path)
    with monkeypatch.context() as m:
        m.setattr(seed, "reset_to_seed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert live.post("/reset", data=GOOD).status_code == 500
    assert live.post("/reset", data=GOOD, follow_redirects=False).status_code == 303  # no 429
    assert is_seed_state(db_path)


# ---- page and headers ----

def test_confirm_page_content(live):
    r = live.get("/reset/confirm")
    html = r.text
    assert "everyone" in html.lower()
    assert 'method="post"' in html and 'action="/reset"' in html
    assert 'name="confirm" value="reset"' in html
    assert "Yes, reset for everyone" in html
    assert 'href="/">Cancel' in html
    assert "no-store" in r.headers["cache-control"]


def test_confirm_page_does_not_change_anything(live, db_path):
    mutate(db_path)
    live.get("/reset/confirm")
    assert not is_seed_state(db_path)


def test_security_headers_on_every_reset_response(live):
    assert_secure(live.get("/reset/confirm"))
    assert_secure(live.get("/reset"))                                                  # 405
    assert_secure(live.post("/reset", data={"confirm": "no"}))                         # 400
    assert_secure(live.post("/reset", data=GOOD, headers={"Origin": "https://x.example"}))  # 403
    assert_secure(live.post("/reset", data=GOOD, follow_redirects=False))              # 303
    assert_secure(live.post("/reset", data=GOOD, follow_redirects=False))              # 429
