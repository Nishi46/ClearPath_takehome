import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import db, seed
from app.seed import reset_to_seed, seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")


def snapshot(c):
    out = {}
    for t in TABLES:
        cur = c.execute(f"SELECT * FROM {t} ORDER BY id")
        out[t] = [tuple(r) for r in cur.fetchall()]
    return out


def counts(c):
    return {t: len(rows) for t, rows in snapshot(c).items()}


def add_user_data(c):
    """What a demo user might do: new submission, new version, decision, comment, flag, dismissal, edits."""
    c.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by, created_at,"
              " current_version) VALUES (15, 'User item', 'loan', 'email', 'in_review', '2026-12-01', 'Me', 'x', 1)")
    c.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (15, 1, 'copy', 'x')")
    vid = c.execute("SELECT id FROM version WHERE submission_id = 15").fetchone()[0]
    c.execute("INSERT INTO flag (version_id, rule_id, severity, kind, matched_text, start_index, end_index)"
              " VALUES (?, 'R1', 'high', 'phrase', 'abc', 0, 3)", (vid,))
    c.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R5', 'medium', 'missing')", (vid,))
    c.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
              " VALUES (?, 'R1', 'n', 'Me', 'x')", (vid,))
    c.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
              " VALUES (15, 1, 'rejected', 'Me', 'why', 'x')")
    c.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at)"
              " VALUES (15, 1, 'Me', 'hello', 'x')")
    c.execute("UPDATE submission SET status = 'approved', title = 'Changed' WHERE id = 1")
    c.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (3, 2, 'v2', 'x')")
    c.execute("DELETE FROM comment WHERE submission_id = 5")
    c.commit()


@pytest.fixture
def fresh(conn):
    seed_all(conn, NOW)
    return conn


def test_reset_restores_exactly_the_fresh_seed(fresh):
    baseline = snapshot(fresh)
    add_user_data(fresh)
    assert snapshot(fresh) != baseline
    reset_to_seed(fresh, NOW)
    assert snapshot(fresh) == baseline


def test_reset_twice_is_byte_identical_with_a_frozen_clock(fresh):
    reset_to_seed(fresh, NOW)
    first = snapshot(fresh)
    reset_to_seed(fresh, NOW)
    assert snapshot(fresh) == first


def test_reset_twice_with_the_real_clock_differs_only_in_timestamps(fresh):
    reset_to_seed(fresh)
    a = snapshot(fresh)
    reset_to_seed(fresh)
    b = snapshot(fresh)
    iso = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?")
    for t in TABLES:
        assert len(a[t]) == len(b[t])
        for ra, rb in zip(a[t], b[t]):
            for x, y in zip(ra, rb):
                if x != y:
                    assert isinstance(x, str) and iso.fullmatch(x) and iso.fullmatch(y), (t, x, y)
                    if "T" in x:
                        delta = (datetime.strptime(y, "%Y-%m-%dT%H:%M:%SZ")
                                 - datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ")).total_seconds()
                        assert 0 <= delta < 10


def test_ids_are_identical_after_reset(fresh):
    before_versions = fresh.execute("SELECT id, submission_id, version_number FROM version ORDER BY id").fetchall()
    add_user_data(fresh)  # pushes ids past the seed range
    reset_to_seed(fresh, NOW)
    assert fresh.execute("SELECT id FROM submission WHERE title = 'Balance transfer email'").fetchone()[0] == 5
    assert fresh.execute("SELECT id FROM submission WHERE title LIKE 'Mortgage prequal%'").fetchone()[0] == 3
    after = fresh.execute("SELECT id, submission_id, version_number FROM version ORDER BY id").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before_versions]
    assert fresh.execute("SELECT count(*) FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()[0] == 0


def test_no_orphans_or_user_rows_survive(fresh):
    add_user_data(fresh)
    reset_to_seed(fresh, NOW)
    assert counts(fresh) == {"submission": 14, "version": 16, "flag": 0, "flag_dismissal": 1,
                             "decision": 7, "comment": 14}
    assert fresh.execute("SELECT count(*) FROM submission WHERE id = 15").fetchone()[0] == 0
    assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []
    assert fresh.execute("SELECT count(*) FROM version v LEFT JOIN submission s ON s.id = v.submission_id"
                         " WHERE s.id IS NULL").fetchone()[0] == 0
    assert fresh.execute("SELECT count(*) FROM comment WHERE text = 'hello'").fetchone()[0] == 0


def test_reset_on_an_empty_database_just_seeds(conn):
    reset_to_seed(conn, NOW)
    assert counts(conn)["submission"] == 14


def test_failure_mid_reinsert_rolls_back_to_the_data_as_it_was(fresh, monkeypatch):
    add_user_data(fresh)
    before = snapshot(fresh)
    real = seed.insert_submission
    n = {"calls": 0}

    def flaky(c, sub):
        n["calls"] += 1
        if n["calls"] == 5:
            raise RuntimeError("boom")
        return real(c, sub)

    monkeypatch.setattr(seed, "insert_submission", flaky)
    with pytest.raises(RuntimeError, match="boom"):
        reset_to_seed(fresh, NOW)
    assert snapshot(fresh) == before  # not empty, not half-seeded
    assert not fresh.in_transaction


def test_failure_in_the_delete_phase_rolls_back(fresh, monkeypatch):
    add_user_data(fresh)
    before = snapshot(fresh)
    monkeypatch.setattr(seed, "seed_rows", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk")))
    with pytest.raises(sqlite3.OperationalError):
        reset_to_seed(fresh, NOW)
    assert snapshot(fresh) == before


def test_bad_seed_file_changes_nothing(fresh, tmp_path, monkeypatch):
    add_user_data(fresh)
    before = snapshot(fresh)
    bad = tmp_path / "seed.json"
    bad.write_text("{oops")
    monkeypatch.setattr(seed, "SEED_PATH", bad)
    with pytest.raises(seed.SeedError):
        reset_to_seed(fresh, NOW)
    assert snapshot(fresh) == before
    assert not fresh.in_transaction


def test_reset_commits_and_is_visible_to_other_connections(fresh, db_path):
    add_user_data(fresh)
    reset_to_seed(fresh, NOW)
    other = sqlite3.connect(str(db_path))
    assert other.execute("SELECT count(*) FROM submission").fetchone()[0] == 14
    assert other.execute("SELECT count(*) FROM submission WHERE id = 15").fetchone()[0] == 0
    other.close()


def test_readers_never_see_an_empty_or_partial_database_during_resets(db_path):
    db.enable_wal()
    with db.connect() as c:
        c.executescript(db.SCHEMA_PATH.read_text())
        seed_all(c, NOW)

    stop = threading.Event()
    seen, errors = set(), []

    def reader():
        r = sqlite3.connect(str(db_path), timeout=10)
        try:
            while not stop.is_set():
                subs = r.execute("SELECT count(*) FROM submission").fetchone()[0]
                seen.add(subs)
                # One read statement is one snapshot: versions never lag the submissions they belong to.
                orphans = r.execute("SELECT count(*) FROM (SELECT s.id, count(v.id) n FROM submission s"
                                    " LEFT JOIN version v ON v.submission_id = s.id GROUP BY s.id HAVING n = 0)"
                                    ).fetchone()[0]
                if orphans:
                    errors.append("submission without versions")
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
    assert seen == {14}


def test_repeated_reset_after_growing_data_stays_at_seed_size(fresh):
    for i in range(3):
        fresh.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by,"
                      " created_at, current_version) VALUES (?, 'x', 'loan', 'email', 'new', '2026-12-01', 'm', 'x', 1)",
                      (100 + i,))
        fresh.commit()
        reset_to_seed(fresh, NOW)
        assert counts(fresh)["submission"] == 14


def test_no_string_built_delete_sql():
    from pathlib import Path
    source = Path(seed.__file__).read_text()
    assert not re.search(r"""f["'][^"']*\bDELETE\b""", source)
