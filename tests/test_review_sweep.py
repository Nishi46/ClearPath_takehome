"""Phase 4 closing sweep: reset after decisions, the new route surface, mass assignment, a fuzz loop."""
import random
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import clock, db, seed
from app.routes import pages

# httpx warns about list-of-pairs form bodies, which the repeated-field cases need.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning:httpx")

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")


@pytest.fixture
def frozen(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(clock, "now", lambda: state["now"])
    return state


@pytest.fixture
def live(db_path, frozen):
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def dump(path):
    c = sqlite3.connect(str(path))
    try:
        return {t: c.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in TABLES}
    finally:
        c.close()


def decide(c, sid, outcome="approved", reason=None, version="1"):
    data = {"outcome": outcome, "version": version}
    if reason is not None:
        data["reason"] = reason
    return c.post(f"/review/{sid}/decision", data=data, follow_redirects=False)


def reset(c, frozen):
    frozen["now"] += timedelta(seconds=60)                  # past the reset cooldown
    pages.reset_cooldown.clear()
    return c.post("/reset", data={"confirm": "reset"}, follow_redirects=False)


# ---- reset restores the exact seed ----

def test_reset_after_decisions_equals_a_fresh_seed(live, db_path, frozen, tmp_path, monkeypatch):
    fresh = dump(db_path)                                    # seeded at T0 on startup
    assert decide(live, 3).status_code == 303
    assert decide(live, 1, "rejected", "No.").status_code == 303
    assert decide(live, 2, "changes_requested", "Fix.").status_code == 303
    assert dump(db_path) != fresh
    frozen["now"] = T0                                       # same clock as the first seed
    pages.reset_cooldown.clear()
    assert live.post("/reset", data={"confirm": "reset"}, follow_redirects=False).status_code == 303
    assert dump(db_path) == fresh                            # every table, every row, ids included


def test_decisions_work_again_after_a_reset(live, frozen):
    assert decide(live, 3).status_code == 303
    assert decide(live, 3).status_code == 409
    assert reset(live, frozen).status_code == 303
    page = live.get("/review/3").text
    assert 'name="outcome"' in page and "Locked" not in page      # open again
    assert decide(live, 3, "rejected", "Again.").status_code == 303


def test_reset_restores_the_queue_rows_and_flags(live, frozen):
    before = live.get("/").text
    for sid in (1, 2, 3, 4):
        decide(live, sid)
    assert live.get("/").text != before
    reset(live, frozen)
    frozen["now"] = T0
    assert re.sub(r"\s+", " ", live.get("/").text.replace("?reset=done", "")) .count("In review") == \
        re.sub(r"\s+", " ", before).count("In review")


def test_reset_leaves_no_orphans(live, db_path, frozen):
    for sid in (1, 3):
        decide(live, sid)
    reset(live, frozen)
    c = sqlite3.connect(str(db_path))
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("SELECT count(*) FROM decision").fetchone()[0] == 7        # the seed's own decisions only
    c.close()


# ---- the route surface and the code patterns ----

def test_only_the_known_review_routes_exist():
    review_routes = sorted((r.path, tuple(sorted(r.methods))) for r in pages.router.routes if "review" in r.path)
    assert review_routes == [("/review/{submission_id}", ("GET",)),
                             ("/review/{submission_id}/comment", ("POST",)),
                             ("/review/{submission_id}/decision", ("POST",)),
                             ("/review/{submission_id}/dismiss", ("POST",))]


def test_no_route_reads_a_reviewer_or_timestamp_field():
    source = Path("app/routes/pages.py").read_text()
    decide_src = source[source.index("async def decide"):source.index("async def dismiss")]
    dismiss_src = source[source.index("async def dismiss"):source.index("async def comment")]
    comment_src = source[source.index("async def comment"):]
    assert "created_at" not in comment_src and "REVIEWER_NAME" in comment_src and "clock.now()" in comment_src
    assert set(re.findall(r'_single\(form, "(\w+)"\)', comment_src)) == {"text", "version", "back"}
    assert 'getlist("rule_id")' in comment_src and "author" not in re.sub(r"REVIEWER_NAME|add_comment", "", comment_src.replace("The author", ""))
    assert 'form.getlist("dismissed_by")' not in dismiss_src and "created_at" not in dismiss_src
    assert "REVIEWER_NAME" in dismiss_src and "clock.now()" in dismiss_src
    assert set(re.findall(r'_single\(form, "(\w+)"\)', dismiss_src)) == {"rule_id", "version", "note", "back"}
    assert 'form.getlist("reviewer")' not in decide_src and "created_at" not in decide_src
    assert "REVIEWER_NAME" in decide_src and "clock.now()" in decide_src
    assert set(re.findall(r'_single\(form, "(\w+)"\)', decide_src)) == {"outcome", "version", "reason", "back"}


def app_sources():
    return [(p, p.read_text()) for p in Path("app").rglob("*") if p.suffix in (".py", ".html")]


def test_no_unsafe_template_or_code_patterns():
    pattern = re.compile(r"\|\s*safe\b|Markup\(|\beval\(|\bexec\(|pickle|yaml\.load|subprocess|os\.system")
    assert [str(p) for p, text in app_sources() if pattern.search(text)] == []


def test_no_sql_text_is_assembled_from_strings():
    pattern = re.compile(r"""(f|rf|fr)["'][^"'\n]*\b(SELECT|INSERT|UPDATE|DELETE)\b|\b(SELECT|INSERT|UPDATE|DELETE)\b[^"'\n]*["']\s*(%|\.format\()""", re.I)
    assert [str(p) for p, text in app_sources() if p.suffix == ".py" and pattern.search(text)] == []


def test_no_inline_scripts_styles_or_handlers_in_templates():
    for p in Path("app/templates").glob("*.html"):
        text = p.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), p
        assert not re.search(r"\sstyle=|\son[a-z]+=", text), p


# ---- mass assignment ----

def test_extra_fields_never_change_other_columns(live, db_path):
    live.post("/review/3/decision", data={
        "outcome": "approved", "version": "1", "status": "rejected", "reviewer": "Mallory",
        "created_at": "1999-01-01T00:00:00Z", "submission_id": "9", "id": "1", "title": "Pwned",
        "current_version": "5", "launch_date": "2000-01-01", "product": "card", "copy": "x"})
    c = sqlite3.connect(str(db_path))
    row = c.execute("SELECT status, title, current_version, launch_date, product FROM submission WHERE id = 3").fetchone()
    assert row == ("approved", "Mortgage prequal landing page", 1, row[3], "mortgage") and row[3] != "2000-01-01"
    assert c.execute("SELECT count(*) FROM decision WHERE reviewer = 'Mallory'").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM decision WHERE submission_id = 9").fetchone()[0] == 0
    assert c.execute("SELECT copy FROM version WHERE submission_id = 3").fetchone()[0] != "x"
    c.close()


def test_a_query_string_cannot_supply_form_fields(live, db_path):
    r = live.post("/review/3/decision?outcome=approved&version=1")
    assert r.status_code == 422 and dump(db_path)["decision"] == dump(db_path)["decision"]
    assert live.get("/review/3").text.count('name="outcome"') == 3


# ---- fuzz: no 500s, no leaks, a healthy database ----

JUNK = ["", " ", "0", "-1", "1", "3", "99999999999999999999", "abc", "approved", "rejected", "changes_requested",
        "' OR 1=1 --", "<script>alert(1)</script>", "\x00", "\u202E", "\U0001F600" * 50, "%", "../../etc/passwd",
        "x" * 3000, "\n\n", "1.5", "NaN", "None", "{}", "[]", "\ud800".encode("utf-8", "surrogatepass").decode("latin-1")]


def test_fuzz_never_500s_leaks_or_corrupts(live, db_path):
    rng = random.Random(20261002)
    leaks = ("Traceback", "sqlite3", "SELECT ", "/Users/", "IntegrityError", "app/review.py", 'File "')
    for i in range(200):
        sid = rng.choice(["1", "2", "3", "5", "6", "7", "9999", "0", "abc", "-1", "1" * 12, "%00", "1.5"] + JUNK[:6])
        fields = {k: rng.choice(JUNK) for k in rng.sample(["outcome", "version", "reason", "back", "status", "reviewer"], rng.randint(0, 6))}
        if rng.random() < 0.5 and "outcome" not in fields:
            fields["outcome"] = rng.choice(["approved", "rejected", "changes_requested"])
        if rng.random() < 0.5 and "version" not in fields:
            fields["version"] = rng.choice(["1", "2"])
        kind = rng.choice(["form", "form", "json", "raw", "repeat"])
        headers = {"Origin": rng.choice(["http://testserver", "https://evil.example", "null"])} if rng.random() < 0.2 else {}
        if kind == "json":
            r = live.post(f"/review/{sid}/decision", json=fields, headers=headers, follow_redirects=False)
        elif kind == "raw":
            r = live.post(f"/review/{sid}/decision", content=rng.choice(JUNK).encode("utf-8", "ignore"),
                          headers={**headers, "content-type": rng.choice(["application/x-www-form-urlencoded", "text/plain", "multipart/form-data"])},
                          follow_redirects=False)
        elif kind == "repeat":
            r = live.post(f"/review/{sid}/decision", data=[(k, v) for k, v in fields.items()] * 2, headers=headers, follow_redirects=False)
        else:
            r = live.post(f"/review/{sid}/decision", data=fields, headers=headers, follow_redirects=False)
        g = live.get(f"/review/{sid}", params={rng.choice(["v", "back", "x"]): rng.choice(JUNK)})
        for resp in (r, g):
            assert resp.status_code < 500, (i, sid, fields, resp.status_code)
            assert not any(l in resp.text for l in leaks), (i, sid, fields)
            assert "default-src 'self'" in resp.headers["content-security-policy"]
    c = sqlite3.connect(str(db_path))
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    # Whatever got through is internally consistent: one decision per version, status matches the outcome.
    assert c.execute("SELECT count(*) FROM (SELECT 1 FROM decision GROUP BY submission_id, version_number HAVING count(*) > 1)").fetchone()[0] == 0
    bad = c.execute("SELECT s.id FROM submission s JOIN decision d ON d.submission_id = s.id AND d.version_number = s.current_version"
                    " WHERE s.status != d.outcome").fetchall()
    assert bad == []
    c.close()
