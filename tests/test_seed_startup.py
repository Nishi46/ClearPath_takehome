import logging
import sqlite3
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, seed

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)


def rows(db_path, sql="SELECT count(*) FROM submission"):
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def start(db_path):
    from app.main import app
    return TestClient(app)


def test_missing_db_file_is_created_and_seeded(db_path):
    assert not db_path.exists()
    with start(db_path) as client:
        assert client.get("/healthz").status_code == 200
    assert rows(db_path) == 14
    assert rows(db_path, "SELECT count(*) FROM version") == 16
    assert rows(db_path, "PRAGMA integrity_check") == "ok"


def test_start_twice_does_not_duplicate(db_path):
    with start(db_path):
        pass
    with start(db_path):
        pass
    assert rows(db_path) == 14
    assert rows(db_path, "SELECT count(*) FROM decision") == 7


def test_restart_does_not_overwrite_changes(db_path):
    with start(db_path):
        pass
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE submission SET status = 'approved', title = 'Edited' WHERE id = 1")
    c.commit()
    c.close()
    with start(db_path):
        pass
    assert rows(db_path, "SELECT status FROM submission WHERE id = 1") == "approved"
    assert rows(db_path, "SELECT title FROM submission WHERE id = 1") == "Edited"
    assert rows(db_path) == 14


def test_existing_user_data_is_not_reseeded(db_path):
    db.init_schema()
    with db.connect() as c:
        c.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by,"
                  " created_at, current_version) VALUES (50, 'Mine', 'loan', 'email', 'new', '2026-12-01', 'me', 'x', 1)")
        c.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (50, 1, 'c', 'x')")
    with start(db_path):
        pass
    assert rows(db_path) == 1
    assert rows(db_path, "SELECT title FROM submission") == "Mine"


def test_concurrent_seed_if_empty_seeds_exactly_once(db_path):
    db.init_schema()
    results, errors = [], []
    barrier = threading.Barrier(4)

    def worker():
        try:
            with db.connect() as c:
                barrier.wait()
                results.append(seed.seed_if_empty(c, NOW))
        except Exception as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(results) == [False, False, False, True]
    assert rows(db_path) == 14
    assert rows(db_path, "SELECT count(*) FROM version") == 16


def test_seed_if_empty_returns_true_then_false(db_path):
    db.init_schema()
    with db.connect() as c:
        assert seed.seed_if_empty(c, NOW) is True
        assert seed.seed_if_empty(c, NOW) is False
        assert not c.in_transaction


def test_populated_db_does_not_even_read_the_seed_file(db_path, monkeypatch, tmp_path):
    with start(db_path):
        pass
    monkeypatch.setattr(seed, "SEED_PATH", tmp_path / "gone.json")  # would raise if read
    with start(db_path) as client:
        assert client.get("/healthz").status_code == 200


def test_broken_seed_file_fails_startup_with_a_logged_message(db_path, tmp_path, monkeypatch, caplog):
    bad = tmp_path / "seed.json"
    bad.write_text("{not json")
    monkeypatch.setattr(seed, "SEED_PATH", bad)
    caplog.set_level(logging.ERROR)
    with pytest.raises(seed.SeedError):
        with start(db_path):
            pass
    assert "Could not load the demo seed" in caplog.text
    assert rows(db_path) == 0  # empty, not half-seeded


def test_failure_midway_leaves_no_partial_data_and_does_not_start(db_path, monkeypatch, caplog):
    real = seed.insert_submission
    n = {"calls": 0}

    def flaky(c, sub):
        n["calls"] += 1
        if n["calls"] == 7:
            raise RuntimeError("disk full")
        return real(c, sub)

    monkeypatch.setattr(seed, "insert_submission", flaky)
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError):
        with start(db_path):
            pass
    assert rows(db_path) == 0
    assert rows(db_path, "SELECT count(*) FROM version") == 0
    assert "disk full" in caplog.text  # the real error is in the log


def test_next_start_recovers_after_a_failed_one(db_path, monkeypatch):
    with monkeypatch.context() as m:  # not monkeypatch.undo(), which would also revert CLEARPATH_DB
        m.setattr(seed, "insert_submission", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(RuntimeError):
            with start(db_path):
                pass
    with start(db_path):
        pass
    assert rows(db_path) == 14


def test_error_details_are_not_served_to_clients(db_path, tmp_path, monkeypatch):
    # Startup fails before any request can be served, so a client never sees seed errors.
    bad = tmp_path / "SECRET-PATH" / "seed.json"
    monkeypatch.setattr(seed, "SEED_PATH", bad)
    with pytest.raises(seed.SeedError) as e:
        with start(db_path):
            pass
    assert "SECRET-PATH" not in str(e.value)


def test_importing_the_app_does_not_create_a_db_or_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", str(tmp_path / "never.db"))
    import importlib
    import app.main
    importlib.reload(app.main)
    assert not (tmp_path / "never.db").exists()


def test_client_without_lifespan_does_not_seed(db_path):
    # Fixture-style TestClient (no `with`) skips startup, which keeps older tests unaffected.
    from app.main import app
    TestClient(app).get("/healthz")
    assert not db_path.exists() or rows(db_path, "SELECT count(*) FROM sqlite_master WHERE name='submission'") == 0
