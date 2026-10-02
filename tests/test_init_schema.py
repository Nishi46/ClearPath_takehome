import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from tests.test_schema_submission_version import add_submission, add_version

REPO = Path(__file__).resolve().parent.parent
TABLES = {"submission", "version", "flag", "flag_dismissal", "decision", "comment"}
INDEXES = {
    "idx_submission_status", "idx_submission_launch_date", "idx_version_submission_id",
    "idx_flag_version_id", "idx_comment_submission_id",
}


def names(conn, kind):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'", (kind,))
    return {r["name"] for r in rows}


def test_fresh_db_is_created_on_first_init(db_path):
    assert not db_path.exists()
    db.init_schema()
    assert db_path.exists()


def test_exactly_six_tables_and_expected_indexes(db_path):
    db.init_schema()
    with db.connect() as conn:
        assert names(conn, "table") == TABLES
        assert names(conn, "index") == INDEXES


def test_running_twice_keeps_data(db_path):
    db.init_schema()
    with db.connect() as conn:
        sid = add_submission(conn, title="Keep me")
        add_version(conn, sid)
    db.init_schema()
    with db.connect() as conn:
        assert conn.execute("SELECT title FROM submission").fetchone()[0] == "Keep me"
        assert conn.execute("SELECT COUNT(*) FROM version").fetchone()[0] == 1
        assert names(conn, "table") == TABLES


def test_user_version_marker_and_wal(db_path):
    db.init_schema()
    db.init_schema()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_works_from_a_different_working_directory(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = tmp_path / "x.db"
    env = dict(os.environ, CLEARPATH_DB=str(target), PYTHONPATH=str(REPO))
    subprocess.run([sys.executable, "-c", "from app import db; db.init_schema()"],
                   cwd=str(elsewhere), env=env, check=True)
    assert target.exists()
    assert list(elsewhere.iterdir()) == []  # nothing written relative to the cwd


def test_startup_creates_tables_through_testclient(db_path):
    from app.main import app

    with TestClient(app) as client:
        client.get("/does-not-exist")
        with db.connect() as conn:
            assert names(conn, "table") == TABLES


def test_startup_twice_on_one_db(db_path):
    from app.main import app

    for _ in range(2):
        with TestClient(app):
            pass
    with db.connect() as conn:
        assert names(conn, "table") == TABLES


def test_import_does_not_create_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", str(tmp_path / "nope.db"))
    env = dict(os.environ, PYTHONPATH=str(REPO), CLEARPATH_DB=str(tmp_path / "nope.db"))
    subprocess.run([sys.executable, "-c", "import app.main"], cwd=str(tmp_path), env=env, check=True)
    assert not (tmp_path / "nope.db").exists()


@pytest.fixture
def read_only_dir(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    yield folder
    folder.chmod(stat.S_IRWXU)
    for f in folder.iterdir():
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_read_only_location_gives_clear_startup_error(read_only_dir, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", str(read_only_dir / "x.db"))
    read_only_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    with pytest.raises(db.DatabaseError, match="Cannot"):
        db.init_schema()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_read_only_existing_db_blocks_app_startup(read_only_dir, monkeypatch):
    from app.main import app

    path = read_only_dir / "x.db"
    monkeypatch.setenv("CLEARPATH_DB", str(path))
    db.init_schema()
    path.chmod(stat.S_IRUSR)
    read_only_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    with pytest.raises(db.DatabaseError):
        with TestClient(app):
            pytest.fail("app must not finish starting on a read-only database")
