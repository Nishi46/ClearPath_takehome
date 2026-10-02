"""Flags through the whole life of the demo database: first start, restart, reset and concurrency."""
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, rules, seed
from app.seed import reset_to_seed, seed_all, seed_if_empty

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
FLAG_ROWS = 25
FLAG_SQL = "SELECT * FROM flag ORDER BY id"


def flag_dump(c):
    return [tuple(r) for r in c.execute(FLAG_SQL).fetchall()]


def everything(c):
    return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in ("submission", "version", "flag", "flag_dismissal", "decision", "comment")}


def scalar(db_path, sql):
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def start(db_path):
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fresh(conn):
    seed_all(conn, NOW)
    return conn


def mess_up_flags(c):
    """What could drift: an extra flag row, a deleted one, edited copy, a changed flag."""
    vid = c.execute("SELECT id FROM version WHERE submission_id = 6").fetchone()[0]
    c.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R3', 'high', 'missing')", (vid,))
    c.execute("DELETE FROM flag WHERE rule_id = 'R6'")
    c.execute("UPDATE version SET copy = 'No disclosures at all, just guaranteed approval' WHERE submission_id = 10")
    c.execute("UPDATE flag SET severity = 'low', matched_text = 'tampered' WHERE rule_id = 'R1' AND kind = 'phrase'")
    c.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by, created_at,"
              " current_version) VALUES (15, 'User', 'loan', 'email', 'new', '2026-12-01', 'me', 'x', 1)")
    c.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (15, 1, 'c', 'x')")
    uv = c.execute("SELECT id FROM version WHERE submission_id = 15").fetchone()[0]
    c.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R5', 'medium', 'missing')", (uv,))
    c.commit()


# ---- reset ----

def test_reset_restores_the_seeded_flags_after_every_kind_of_drift(fresh):
    baseline = everything(fresh)
    assert len(baseline["flag"]) == FLAG_ROWS
    mess_up_flags(fresh)
    assert flag_dump(fresh) != baseline["flag"]
    reset_to_seed(fresh, NOW)
    assert everything(fresh) == baseline


def test_reset_twice_gives_identical_flag_rows_including_ids(fresh):
    reset_to_seed(fresh, NOW)
    first = flag_dump(fresh)
    reset_to_seed(fresh, NOW)
    assert flag_dump(fresh) == first
    assert [r[0] for r in first] == list(range(1, FLAG_ROWS + 1))  # ids restart; no drift


def test_flags_do_not_depend_on_the_clock(fresh):
    baseline = [r[1:] for r in flag_dump(fresh)]  # drop the row id
    reset_to_seed(fresh, NOW + timedelta(days=40, hours=5))
    assert [r[1:] for r in flag_dump(fresh)] == baseline


def test_no_orphans_and_no_user_flags_survive(fresh):
    mess_up_flags(fresh)
    reset_to_seed(fresh, NOW)
    assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []
    assert fresh.execute("SELECT count(*) FROM flag f LEFT JOIN version v ON v.id = f.version_id"
                         " WHERE v.id IS NULL").fetchone()[0] == 0
    assert fresh.execute("SELECT count(*) FROM flag WHERE matched_text = 'tampered'").fetchone()[0] == 0
    assert fresh.execute("SELECT count(*) FROM submission WHERE id = 15").fetchone()[0] == 0
    assert fresh.execute("SELECT count(*) FROM flag").fetchone()[0] == FLAG_ROWS


def test_a_user_added_version_with_flags_is_removed_by_reset(fresh):
    fresh.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (3, 2, 'v2', 'x')")
    vid = fresh.execute("SELECT id FROM version WHERE submission_id = 3 AND version_number = 2").fetchone()[0]
    fresh.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R3', 'high', 'missing')", (vid,))
    fresh.commit()
    reset_to_seed(fresh, NOW)
    assert fresh.execute("SELECT count(*) FROM version WHERE submission_id = 3").fetchone()[0] == 1
    assert fresh.execute("SELECT count(*) FROM flag").fetchone()[0] == FLAG_ROWS


def test_an_engine_failure_during_reset_rolls_back_to_the_data_as_it_was(fresh, monkeypatch):
    mess_up_flags(fresh)
    before = everything(fresh)
    real = seed.evaluate_version
    calls = []

    def flaky(c, vid):
        calls.append(vid)
        if len(calls) == 11:
            raise RuntimeError("engine failed")
        return real(c, vid)

    monkeypatch.setattr(seed, "evaluate_version", flaky)
    with pytest.raises(RuntimeError):
        reset_to_seed(fresh, NOW)
    assert everything(fresh) == before  # the user's data and flags are intact, not half-seeded
    assert not fresh.in_transaction


def test_readers_never_see_submissions_without_their_flags_during_resets(db_path):
    db.enable_wal()
    with db.connect() as c:
        c.executescript(db.SCHEMA_PATH.read_text())
        seed_all(c, NOW)
    stop, seen, errors = threading.Event(), set(), []

    def reader():
        r = sqlite3.connect(str(db_path), timeout=10)
        try:
            while not stop.is_set():
                # One statement is one snapshot, so the two counts always belong together.
                seen.add(tuple(r.execute("SELECT (SELECT count(*) FROM submission), (SELECT count(*) FROM flag)").fetchone()))
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))
        finally:
            r.close()

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        for _ in range(25):
            with db.connect() as c:
                reset_to_seed(c, NOW)
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert errors == []
    assert seen == {(14, FLAG_ROWS)}, seen


def test_reset_through_the_route_restores_the_flags(db_path):
    with start(db_path) as client:
        c = sqlite3.connect(str(db_path))
        c.execute("DELETE FROM flag")
        c.execute("UPDATE submission SET title = 'Changed' WHERE id = 1")
        c.commit()
        c.close()
        assert scalar(db_path, "SELECT count(*) FROM flag") == 0
        r = client.post("/reset", data={"confirm": "reset"}, follow_redirects=False)
        assert r.status_code == 303
    assert scalar(db_path, "SELECT count(*) FROM flag") == FLAG_ROWS
    assert scalar(db_path, "SELECT title FROM submission WHERE id = 1") == "Personal loan holiday email"


# ---- first start and restart ----

def test_first_start_seeds_the_flags(db_path):
    with start(db_path) as client:
        assert client.get("/healthz").status_code == 200
    assert scalar(db_path, "SELECT count(*) FROM flag") == FLAG_ROWS
    assert scalar(db_path, "PRAGMA integrity_check") == "ok"
    assert scalar(db_path, "SELECT count(*) FROM flag f LEFT JOIN version v ON v.id = f.version_id WHERE v.id IS NULL") == 0


def test_restart_does_not_duplicate_or_recompute_flags(db_path):
    with start(db_path):
        pass
    c = sqlite3.connect(str(db_path))
    first = flag_dump(c)
    c.execute("DELETE FROM flag WHERE rule_id = 'R7'")  # a deliberate change must survive a restart
    c.commit()
    remaining = flag_dump(c)
    c.close()
    for _ in range(2):
        with start(db_path):
            pass
    c = sqlite3.connect(str(db_path))
    assert flag_dump(c) == remaining and len(remaining) < len(first)
    c.close()


def test_restart_with_untouched_flags_leaves_them_byte_identical(db_path):
    with start(db_path):
        pass
    c = sqlite3.connect(str(db_path))
    first = flag_dump(c)
    c.close()
    with start(db_path):
        pass
    c = sqlite3.connect(str(db_path))
    assert flag_dump(c) == first
    c.close()


def test_concurrent_seed_if_empty_produces_one_set_of_flags(db_path):
    db.init_schema()
    results, errors = [], []
    barrier = threading.Barrier(4)

    def worker():
        try:
            with db.connect() as c:
                barrier.wait()
                results.append(seed_if_empty(c, NOW))
        except Exception as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and sorted(results) == [False, False, False, True]
    assert scalar(db_path, "SELECT count(*) FROM flag") == FLAG_ROWS
    assert scalar(db_path, "SELECT count(*) FROM (SELECT version_id, rule_id, start_index, count(*) n FROM flag"
                           " GROUP BY version_id, rule_id, start_index HAVING n > 1)") == 0


# ---- a broken rules file ----

def test_a_broken_rules_file_fails_startup_with_a_logged_message(db_path, tmp_path, monkeypatch, caplog):
    bad = tmp_path / "rules.json"
    bad.write_text("[{not json")
    monkeypatch.setattr(rules, "RULES_PATH", bad)
    monkeypatch.setattr(rules, "_cache", None)
    caplog.set_level(logging.ERROR)
    client = start(db_path)
    with pytest.raises(rules.RulesError):
        with client:
            pass
    assert "Could not load the demo seed" in caplog.text and "not valid JSON" in caplog.text
    # all or nothing: no submissions, versions or flags were left behind
    for table in ("submission", "version", "flag", "decision", "comment", "flag_dismissal"):
        assert scalar(db_path, f"SELECT count(*) FROM {table}") == 0, table


def test_a_missing_rules_file_also_fails_startup_and_the_next_start_recovers(db_path, tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(rules, "RULES_PATH", tmp_path / "gone.json")
        m.setattr(rules, "_cache", None)
        with pytest.raises(rules.RulesError):
            with start(db_path):
                pass
    monkeypatch.setattr(rules, "_cache", None)
    with start(db_path) as client:
        assert client.get("/healthz").status_code == 200
    assert scalar(db_path, "SELECT count(*) FROM flag") == FLAG_ROWS


def test_a_rules_failure_never_leaves_a_reachable_half_seeded_app(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "RULES_PATH", tmp_path / "gone.json")
    monkeypatch.setattr(rules, "_cache", None)
    with pytest.raises(rules.RulesError):
        with start(db_path) as client:
            client.get("/healthz")  # never reached: startup raised first


def test_rules_error_message_does_not_expose_the_server_path_to_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "RULES_PATH", tmp_path / "SECRET-DIR" / "rules.json")
    monkeypatch.setattr(rules, "_cache", None)
    with pytest.raises(rules.RulesError) as e:
        rules.all_rules()
    assert "SECRET-DIR" not in str(e.value)
