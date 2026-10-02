import re
import sqlite3
from pathlib import Path

import pytest

NOW = "2026-10-01T12:00:00+00:00"


def add_submission(conn, **over):
    row = dict(title="Holiday email", product="loan", channel="email", status="new",
               launch_date="2026-10-10", submitted_by="Maya Chen", created_at=NOW,
               current_version=1)
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return conn.execute(
        "INSERT INTO submission (%s) VALUES (%s)" % (cols, marks), list(row.values())
    ).lastrowid


def add_version(conn, submission_id, version_number=1, **over):
    row = dict(submission_id=submission_id, version_number=version_number,
               copy="Some ad copy", notes=None, created_at=NOW)
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute("INSERT INTO version (%s) VALUES (%s)" % (cols, marks), list(row.values()))


def test_valid_submission_and_version(conn):
    sid = add_submission(conn)
    add_version(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM version WHERE submission_id = ?", (sid,)).fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [
    ("product", "auto"), ("channel", "sms"), ("status", "pending"),
    ("product", ""), ("status", "APPROVED"),
])
def test_invalid_enum_rejected(conn, field, value):
    with pytest.raises(sqlite3.IntegrityError):
        add_submission(conn, **{field: value})


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_title_rejected(conn, value):
    with pytest.raises(sqlite3.IntegrityError):
        add_submission(conn, title=value)


@pytest.mark.parametrize("value", ["", "   ", "\n\t "])
def test_blank_copy_rejected(conn, value):
    sid = add_submission(conn)
    with pytest.raises(sqlite3.IntegrityError):
        add_version(conn, sid, copy=value)


def test_null_required_fields_rejected(conn):
    for field in ("title", "launch_date", "submitted_by", "created_at", "current_version"):
        with pytest.raises(sqlite3.IntegrityError):
            add_submission(conn, **{field: None})


def test_duplicate_version_number_rejected(conn):
    sid = add_submission(conn)
    add_version(conn, sid, 1)
    with pytest.raises(sqlite3.IntegrityError):
        add_version(conn, sid, 1)
    add_version(conn, sid, 2)  # a different number is fine


def test_version_for_missing_submission_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_version(conn, 999)


def test_delete_submission_cascades_to_versions(conn):
    sid = add_submission(conn)
    add_version(conn, sid, 1)
    add_version(conn, sid, 2)
    conn.execute("DELETE FROM submission WHERE id = ?", (sid,))
    assert conn.execute("SELECT COUNT(*) FROM version").fetchone()[0] == 0


def test_notes_may_be_null(conn):
    sid = add_submission(conn)
    add_version(conn, sid, notes=None)


def test_indexes_exist(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_submission_status", "idx_submission_launch_date", "idx_version_submission_id"} <= names


def test_schema_is_idempotent(conn):
    schema = (Path(__file__).parent.parent / "app" / "schema.sql").read_text()
    conn.executescript(schema)
    conn.executescript(schema)


def test_sql_injection_text_stored_literally(conn):
    evil = "'; DROP TABLE submission; --"
    sid = add_submission(conn, title=evil)
    add_version(conn, sid, copy=evil)
    assert conn.execute("SELECT title FROM submission WHERE id = ?", (sid,)).fetchone()[0] == evil
    assert conn.execute("SELECT copy FROM version").fetchone()[0] == evil
    assert conn.execute("SELECT COUNT(*) FROM submission").fetchone()[0] == 1


def test_no_string_built_sql_in_app():
    """Every execute() call in app/ must pass a plain string literal (parameters go in the 2nd arg)."""
    bad = []
    for path in Path(__file__).parent.parent.joinpath("app").rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            m = re.search(r"\.execute(?:many|script)?\(\s*(.*)", line)
            if not m:
                continue
            arg = m.group(1)
            if arg.startswith(("f\"", "f'")) or re.match(r"[\"'][^\"']*[\"']\s*(%|\+|\.format)", arg) \
                    or re.match(r"[a-zA-Z_]", arg) and not arg.startswith(("schema", "sql", "SCHEMA_PATH.read_text()")):
                bad.append("%s:%d: %s" % (path.name, n, line.strip()))
    assert bad == []
