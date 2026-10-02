import inspect
import sqlite3
from pathlib import Path

import pytest

from app import db


def test_default_path_when_env_unset(monkeypatch):
    monkeypatch.delenv("CLEARPATH_DB", raising=False)
    assert db.get_db_path() == "./clearpath.db"


def test_rows_addressable_by_column_name(db_path):
    with db.connect() as conn:
        row = conn.execute("SELECT 1 AS one, 'x' AS two").fetchone()
    assert row["one"] == 1 and row["two"] == "x"


def test_foreign_keys_on_for_every_connection(db_path):
    for _ in range(3):
        with db.connect() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_busy_timeout_is_five_seconds(db_path):
    with db.connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_commit_on_success(db_path):
    with db.connect() as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_rollback_on_exception(db_path):
    with db.connect() as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
    with pytest.raises(ValueError):
        with db.connect() as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise ValueError("boom")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_connection_closed_after_exit(db_path):
    with db.connect() as conn:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connection_closed_after_exception(db_path):
    with pytest.raises(ValueError):
        with db.connect() as conn:
            raise ValueError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_missing_parent_folder_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", str(tmp_path / "nope" / "x.db"))
    with pytest.raises(db.DatabaseError, match="folder does not exist"):
        with db.connect():
            pass


def test_enable_wal(db_path):
    db.enable_wal()
    with db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_db_path_comes_only_from_environment():
    assert list(inspect.signature(db.get_db_path).parameters) == []
    for source in Path(db.__file__).parent.rglob("*.py"):
        text = source.read_text()
        if "get_db_path" in text and source.name != "db.py":
            assert "request" not in text, "%s may pass request data to get_db_path" % source
    db_source = Path(db.__file__).read_text()
    assert "fastapi" not in db_source and "starlette" not in db_source
