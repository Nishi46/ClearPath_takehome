import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, review
from app.seed import seed_all

SCHEMA = (Path(db.__file__).parent / "schema.sql").read_text()
MARKER = "-- Immutable audit rows"
NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
UPDATES = [
    ("decision", "UPDATE decision SET reason = 'changed' WHERE id = (SELECT min(id) FROM decision)"),
    ("decision", "UPDATE decision SET outcome = 'rejected'"),
    ("comment", "UPDATE comment SET text = 'changed' WHERE id = (SELECT min(id) FROM comment)"),
    ("comment", "UPDATE comment SET author = 'Mallory'"),
    ("flag_dismissal", "UPDATE flag_dismissal SET note = 'changed'"),
    ("flag_dismissal", "UPDATE flag_dismissal SET rule_id = 'R9'"),
]


def version_one_schema():
    """The schema as it was before the triggers: everything up to the marker, user_version 1."""
    return SCHEMA.split(MARKER)[0] + "PRAGMA user_version = 1;\n"


def shape(conn):
    return sorted((r["type"], r["name"], r["sql"]) for r in conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    conn.commit()
    return conn


@pytest.mark.parametrize("table, sql", UPDATES)
def test_raw_updates_are_refused_and_leave_the_row_alone(seeded, table, sql):
    before = [tuple(r) for r in seeded.execute("SELECT * FROM %s ORDER BY id" % table)]
    assert before
    with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
        seeded.execute(sql)
    seeded.rollback()
    assert [tuple(r) for r in seeded.execute("SELECT * FROM %s ORDER BY id" % table)] == before


def test_deletes_inserts_and_cascades_still_work(seeded):
    seeded.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at)"
                   " VALUES (3, 1, 'x', 'ok', '2026-10-01T00:00:00Z')")
    seeded.execute("DELETE FROM submission WHERE id = 13")             # cascades through all three tables
    assert seeded.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 0
    seeded.commit()
    from app import seed
    seed.reset_to_seed(seeded)                                         # reset uses DELETE
    assert seeded.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 1


def test_app_writes_still_work_with_the_triggers(seeded):
    review.record_decision(seeded, 3, 1, "approved", None, "Alex Rivera", NOW)
    review.add_comment(seeded, 3, 1, "Fine.", None, "Alex Rivera", NOW)
    review.dismiss_flag(seeded, 12, 1, "R4", "Quoted.", "Alex Rivera", NOW)
    assert seeded.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == "approved"


def test_status_and_flags_can_still_change(seeded):
    seeded.execute("UPDATE submission SET status = 'in_review' WHERE id = 1")
    seeded.execute("UPDATE flag SET severity = 'low' WHERE id = (SELECT min(id) FROM flag)")
    seeded.commit()


def test_an_old_version_one_database_is_upgraded_without_data_loss(db_path):
    raw = sqlite3.connect(db_path)
    raw.executescript(version_one_schema())
    raw.commit()
    raw.close()
    with db.connect() as c:
        seed_all(c, NOW)
        before = {t: [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)]
                  for t in ("submission", "version", "flag", "flag_dismissal", "decision", "comment")}
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not [r for r in c.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger'")]
    db.init_schema()
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
        assert len(list(c.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger'"))) == 3
        for t, rows in before.items():
            assert [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)] == rows
        with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
            c.execute("UPDATE decision SET reason = 'x'")


def test_running_init_three_times_is_a_no_op_and_fresh_equals_upgraded(tmp_path, monkeypatch):
    fresh = tmp_path / "fresh.db"
    old = tmp_path / "old.db"
    monkeypatch.setenv("CLEARPATH_DB", str(fresh))
    for _ in range(3):
        db.init_schema()
    with db.connect() as c:
        fresh_shape = shape(c)
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    raw = sqlite3.connect(old)
    raw.executescript(version_one_schema())
    raw.close()
    monkeypatch.setenv("CLEARPATH_DB", str(old))
    for _ in range(3):
        db.init_schema()
    with db.connect() as c:
        assert shape(c) == fresh_shape


def test_the_marker_section_is_only_triggers_and_the_version():
    tail = re.sub(r"--[^\n]*", "", SCHEMA.split(MARKER)[1])
    assert re.findall(r"CREATE TRIGGER IF NOT EXISTS (\w+) BEFORE UPDATE ON (\w+)", tail) == [
        ("decision_no_update", "decision"), ("comment_no_update", "comment"),
        ("flag_dismissal_no_update", "flag_dismissal")]
    assert tail.count("PRAGMA user_version = 2;") == 1
    assert not re.search(r"\b(CREATE TABLE|CREATE INDEX|INSERT|DELETE|DROP)\b", tail)


def test_no_app_code_updates_the_audit_tables_and_submission_updates_are_the_known_two():
    updates = []
    for path in Path("app").rglob("*.py"):
        for stmt in re.findall(r'"(UPDATE [^"]*)"', path.read_text()):
            updates.append((path.name, stmt))
    assert not [u for u in updates if re.search(r"UPDATE\s+(decision|comment|flag_dismissal)\b", u[1])]
    assert sorted(f for f, s in updates if s.startswith("UPDATE submission")) == ["review.py", "submit.py"]


def test_tamper_helper_restores_the_triggers(seeded):
    from tests.helpers import tamper

    tamper(seeded, "UPDATE comment SET text = 'edited' WHERE id = (SELECT min(id) FROM comment)")
    assert len(list(seeded.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger'"))) == 3
    with pytest.raises(sqlite3.DatabaseError):
        seeded.execute("UPDATE comment SET text = 'again'")
