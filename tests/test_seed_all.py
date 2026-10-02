import sqlite3
from collections import Counter
from datetime import datetime, timezone

import pytest

from app import seed
from app.seed import NotEmptyError, SeedError, seed_all, seed_rows

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
TABLES = ("submission", "version", "decision", "comment", "flag_dismissal", "flag")


def counts(c):
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


def other_connection(db_path):
    return sqlite3.connect(str(db_path))


def test_fresh_db_gets_the_full_seed(conn):
    seed_all(conn, NOW)
    assert counts(conn) == {"submission": 14, "version": 16, "decision": 7, "comment": 14,
                            "flag_dismissal": 1, "flag": 0}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_status_counts_match_the_seed_doc(conn):
    seed_all(conn, NOW)
    got = Counter(r[0] for r in conn.execute("SELECT status FROM submission"))
    assert got == {"new": 6, "in_review": 3, "changes_requested": 1, "approved": 3, "rejected": 1}


def test_uses_relative_dates_from_now(conn):
    seed_all(conn, NOW)
    dates = dict(conn.execute("SELECT id, launch_date FROM submission").fetchall())
    assert dates[11] == "2026-09-30" and dates[1] == "2026-10-02" and dates[12] == "2026-10-31"


def test_defaults_to_the_real_clock(conn):
    from app import clock
    seed_all(conn)
    today = clock.today().isoformat()
    assert conn.execute("SELECT launch_date FROM submission WHERE id = 11").fetchone()[0] < today


def test_data_is_committed_and_visible_to_other_connections(conn, db_path):
    seed_all(conn, NOW)
    other = other_connection(db_path)
    assert other.execute("SELECT count(*) FROM submission").fetchone()[0] == 14
    other.close()
    assert not conn.in_transaction


def test_failure_on_the_9th_submission_leaves_the_db_empty(conn, db_path, monkeypatch):
    real = seed.insert_submission
    calls = {"n": 0}

    def flaky(c, sub):
        calls["n"] += 1
        if calls["n"] == 9:
            raise RuntimeError("boom")
        return real(c, sub)

    monkeypatch.setattr(seed, "insert_submission", flaky)
    with pytest.raises(RuntimeError, match="boom"):
        seed_all(conn, NOW)
    assert calls["n"] == 9
    assert all(v == 0 for v in counts(conn).values())
    assert not conn.in_transaction  # lock released
    other = other_connection(db_path)
    assert other.execute("SELECT count(*) FROM submission").fetchone()[0] == 0
    other.close()


def test_failure_while_inserting_history_also_rolls_back_everything(conn, monkeypatch):
    real = seed.insert_history
    state = {"n": 0}

    def flaky(c, sub):
        state["n"] += 1
        if state["n"] == 14:
            raise sqlite3.IntegrityError("late failure")
        return real(c, sub)

    monkeypatch.setattr(seed, "insert_history", flaky)
    with pytest.raises(sqlite3.IntegrityError):
        seed_all(conn, NOW)
    assert all(v == 0 for v in counts(conn).values())


def test_can_retry_after_a_failure(conn, monkeypatch):
    monkeypatch.setattr(seed, "insert_submission", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        seed_all(conn, NOW)
    monkeypatch.undo()
    seed_all(conn, NOW)
    assert counts(conn)["submission"] == 14


def test_second_call_on_a_populated_db_raises_and_changes_nothing(conn):
    seed_all(conn, NOW)
    conn.execute("UPDATE submission SET status = 'approved' WHERE id = 1")
    conn.commit()
    before = counts(conn)
    with pytest.raises(NotEmptyError):
        seed_all(conn, NOW)
    assert counts(conn) == before
    assert conn.execute("SELECT status FROM submission WHERE id = 1").fetchone()[0] == "approved"
    assert not conn.in_transaction


def test_a_db_with_even_one_submission_is_not_seeded(conn):
    conn.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by,"
                 " created_at, current_version) VALUES (99, 't', 'loan', 'email', 'new', '2026-10-05', 'me', 'x', 1)")
    conn.commit()
    with pytest.raises(NotEmptyError):
        seed_all(conn, NOW)
    assert counts(conn)["submission"] == 1


def test_not_empty_error_is_a_seed_error():
    assert issubclass(NotEmptyError, SeedError)


def test_bad_seed_file_leaves_the_db_untouched_and_takes_no_lock(conn, tmp_path, monkeypatch, db_path):
    bad = tmp_path / "seed.json"
    bad.write_text('{"submissions": [{"seedId": 1}]}')
    monkeypatch.setattr(seed, "SEED_PATH", bad)
    with pytest.raises(SeedError):
        seed_all(conn, NOW)
    assert all(v == 0 for v in counts(conn).values())
    assert not conn.in_transaction  # validation failed before BEGIN IMMEDIATE


def test_missing_seed_file_is_a_clean_error(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "SEED_PATH", tmp_path / "gone.json")
    with pytest.raises(SeedError, match="not found"):
        seed_all(conn, NOW)
    assert counts(conn)["submission"] == 0


def test_seed_all_holds_the_write_lock_while_it_runs(conn, db_path, monkeypatch):
    real = seed.insert_submission
    seen = {}

    def spying(c, sub):
        if sub["seedId"] == 2:
            other = sqlite3.connect(str(db_path), timeout=0.05)
            try:
                other.execute("BEGIN IMMEDIATE")
                seen["blocked"] = False
            except sqlite3.OperationalError:
                seen["blocked"] = True
            finally:
                other.close()
        return real(c, sub)

    monkeypatch.setattr(seed, "insert_submission", spying)
    seed_all(conn, NOW)
    assert seen["blocked"] is True


def test_seed_rows_leaves_transaction_control_to_the_caller(conn, db_path):
    conn.execute("BEGIN IMMEDIATE")
    seed_rows(conn, NOW)
    assert conn.in_transaction
    other = other_connection(db_path)
    assert other.execute("SELECT count(*) FROM submission").fetchone()[0] == 0  # uncommitted
    other.close()
    conn.rollback()
    assert counts(conn)["submission"] == 0


def test_seed_rows_also_refuses_a_populated_db(conn):
    seed_all(conn, NOW)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(NotEmptyError):
        seed_rows(conn, NOW)
    conn.rollback()


def test_seeding_does_not_commit_stray_changes_on_failure(conn, monkeypatch):
    # A failed seed must roll back only its own work, never commit it.
    monkeypatch.setattr(seed, "insert_history", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        seed_all(conn, NOW)
    assert counts(conn)["submission"] == 0
