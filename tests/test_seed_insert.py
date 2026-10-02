import copy
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import seed
from app.seed import insert_submission, load_seed, resolve_times

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def resolved():
    return resolve_times(load_seed(), NOW)


def sub(resolved, seed_id):
    return copy.deepcopy(next(s for s in resolved["submissions"] if s["seedId"] == seed_id))


def test_submission_5_inserts_one_submission_and_two_versions(conn, resolved):
    insert_submission(conn, sub(resolved, 5))
    s = conn.execute("SELECT * FROM submission").fetchall()
    assert len(s) == 1
    assert dict(s[0]) == {
        "id": 5, "title": "Balance transfer email", "product": "card", "channel": "email",
        "status": "in_review", "launch_date": "2026-10-09", "submitted_by": "Maya Chen",
        "created_at": "2026-09-26T12:30:15Z", "current_version": 2,
    }
    v = conn.execute("SELECT * FROM version ORDER BY version_number").fetchall()
    assert [(r["submission_id"], r["version_number"]) for r in v] == [(5, 1), (5, 2)]
    assert [r["created_at"] for r in v] == ["2026-09-26T12:30:15Z", "2026-09-29T20:30:15Z"]
    assert "0% intro interest rate" in v[0]["copy"] and "0% intro APR" in v[1]["copy"]
    assert v[1]["notes"].startswith("Added the APR")


def test_copy_and_notes_are_stored_byte_for_byte(conn, resolved):
    raw = json.loads((Path(seed.__file__).parent.parent / "data" / "seed.json").read_text())
    for src in raw["submissions"]:
        insert_submission(conn, sub(resolved, src["seedId"]))
    for src in raw["submissions"]:
        for v in src["versions"]:
            row = conn.execute("SELECT copy, notes FROM version WHERE submission_id=? AND version_number=?",
                               (src["seedId"], v["versionNumber"])).fetchone()
            assert row["copy"].encode() == v["copy"].encode()
            assert row["notes"].encode() == v["notes"].encode()
    assert "\n" in conn.execute("SELECT copy FROM version WHERE submission_id=1").fetchone()["copy"]


def test_row_id_equals_seed_id_for_all_14(conn, resolved):
    for s in resolved["submissions"]:
        insert_submission(conn, copy.deepcopy(s))
    assert [r["id"] for r in conn.execute("SELECT id FROM submission ORDER BY id")] == list(range(1, 15))
    assert conn.execute("SELECT count(*) FROM version").fetchone()[0] == 16


def test_missing_notes_are_stored_as_null(conn, resolved):
    s = sub(resolved, 1)
    del s["versions"][0]["notes"]
    insert_submission(conn, s)
    assert conn.execute("SELECT notes FROM version").fetchone()["notes"] is None


@pytest.mark.parametrize("payload", [
    "'; DROP TABLE submission; --",
    "<script>alert(1)</script>",
    "Robert'); DROP TABLE version;--",
    '" OR 1=1 --',
    "line1\nline2\ttab \u202E rtl \U0001F600",
])
def test_hostile_text_is_stored_literally_and_tables_survive(conn, resolved, payload):
    s = sub(resolved, 1)
    s["title"] = payload
    s["submittedBy"] = payload
    s["versions"][0]["copy"] = payload
    s["versions"][0]["notes"] = payload
    insert_submission(conn, s)
    row = conn.execute("SELECT s.title, s.submitted_by, v.copy, v.notes FROM submission s JOIN version v"
                       " ON v.submission_id = s.id").fetchone()
    assert tuple(row) == (payload,) * 4
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"submission", "version"} <= tables


def test_same_seed_id_twice_fails_and_keeps_the_first(conn, resolved):
    insert_submission(conn, sub(resolved, 3))
    with pytest.raises(sqlite3.IntegrityError):
        insert_submission(conn, sub(resolved, 3))
    assert conn.execute("SELECT count(*) FROM submission").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM version").fetchone()[0] == 1


def test_foreign_keys_are_on_for_this_connection(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO version (submission_id, version_number, copy, created_at)"
                     " VALUES (99, 1, 'x', 'now')")


def test_database_rejects_a_bad_value_and_nothing_is_left_behind(conn, resolved):
    s = sub(resolved, 5)
    s["versions"][1]["copy"] = "   "  # the CHECK constraint refuses blank copy
    conn.execute("SAVEPOINT t")
    with pytest.raises(sqlite3.IntegrityError):
        insert_submission(conn, s)
    conn.execute("ROLLBACK TO t")
    assert conn.execute("SELECT count(*) FROM submission").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM version").fetchone()[0] == 0


def test_no_flags_decisions_or_comments_are_inserted(conn, resolved):
    insert_submission(conn, sub(resolved, 14))
    for table in ("flag", "flag_dismissal", "decision", "comment"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_insert_does_not_mutate_its_input(conn, resolved):
    s = sub(resolved, 5)
    before = copy.deepcopy(s)
    insert_submission(conn, s)
    assert s == before


def test_insert_does_not_commit(db_path, resolved):
    from app import db
    from app.seed import insert_submission as ins

    schema = (Path(db.__file__).parent / "schema.sql").read_text()
    with db.connect() as c:
        c.executescript(schema)
    c = sqlite3.connect(str(db_path))
    try:
        c.execute("PRAGMA foreign_keys = ON")
        ins(c, sub(resolved, 1))
        other = sqlite3.connect(str(db_path))
        assert other.execute("SELECT count(*) FROM submission").fetchone()[0] == 0  # uncommitted
        other.close()
        c.rollback()
    finally:
        c.close()


def test_sql_is_parameterized_in_seed_module():
    source = Path(seed.__file__).read_text()
    assert not re.search(r"""f["'][^"']*\b(INSERT|SELECT|UPDATE|DELETE)\b""", source)
    assert not re.search(r"""\b(INSERT|SELECT|UPDATE|DELETE)\b[^\n]*["']\s*%""", source)
    assert not re.search(r"""\b(INSERT|SELECT|UPDATE|DELETE)\b[^\n]*\.format\(""", source)
