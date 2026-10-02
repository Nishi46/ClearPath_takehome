import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, review
from app.review import DecisionError, record_decision
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 2, 9, 5, 0, tzinfo=timezone.utc)
ME = "Alex Rivera"


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def state(c):
    return (
        [tuple(r) for r in c.execute("SELECT id, status, current_version FROM submission ORDER BY id")],
        [tuple(r) for r in c.execute("SELECT * FROM decision ORDER BY id")],
    )


def decide(c, sid=3, v=1, outcome="approved", reason=None, who=ME, now=LATER):
    return record_decision(c, sid, v, outcome, reason, who, now)


def refused(c, code, *a, **k):
    before = state(c)
    with pytest.raises(DecisionError) as e:
        decide(c, *a, **k)
    assert e.value.code == code
    assert state(c) == before
    assert not c.in_transaction


# ---- happy paths ----

def test_approve_3(seeded):
    r = decide(seeded)
    assert r == {"submission_id": 3, "version_number": 1, "outcome": "approved", "reviewer": ME,
                 "reason": None, "created_at": "2026-10-02T09:05:00Z"}
    row = seeded.execute("SELECT * FROM decision WHERE submission_id = 3").fetchone()
    assert (row["outcome"], row["reviewer"], row["reason"], row["created_at"]) == ("approved", ME, None, "2026-10-02T09:05:00Z")
    assert seeded.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == "approved"


@pytest.mark.parametrize("outcome", ["changes_requested", "rejected"])
def test_negative_outcomes_with_reason(seeded, outcome):
    decide(seeded, outcome=outcome, reason="  Please add the APR.  ")
    row = seeded.execute("SELECT reason FROM decision WHERE submission_id = 3").fetchone()
    assert row[0] == "Please add the APR."
    assert seeded.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == outcome


def test_new_item_can_be_decided_directly(seeded):
    decide(seeded, sid=1)
    assert seeded.execute("SELECT status FROM submission WHERE id = 1").fetchone()[0] == "approved"


def test_item_5_current_version_is_decidable(seeded):
    decide(seeded, sid=5, v=2, outcome="rejected", reason="No.")


def test_other_rows_untouched(seeded):
    before = state(seeded)
    decide(seeded)
    after = state(seeded)
    assert [r for r in after[0] if r[0] != 3] == [r for r in before[0] if r[0] != 3]
    assert len(after[1]) == len(before[1]) + 1


def test_committed_not_left_open(seeded, db_path):
    decide(seeded)
    assert not seeded.in_transaction
    with db.connect() as other:
        assert other.execute("SELECT count(*) FROM decision WHERE submission_id = 3").fetchone()[0] == 1


# ---- reasons ----

@pytest.mark.parametrize("outcome", ["changes_requested", "rejected"])
@pytest.mark.parametrize("reason", [None, "", "   ", "\t\n\r", "​", "​  ⁠﻿", " ", "\x00"])
def test_blank_reasons_are_refused(seeded, outcome, reason):
    refused(seeded, "reason_required", outcome=outcome, reason=reason)


def test_approve_reason_optional_and_trimmed_or_null(seeded):
    decide(seeded, sid=1, reason="   ")
    decide(seeded, sid=3, reason="  Clean.  ")
    assert seeded.execute("SELECT reason FROM decision WHERE submission_id = 1").fetchone()[0] is None
    assert seeded.execute("SELECT reason FROM decision WHERE submission_id = 3").fetchone()[0] == "Clean."


def test_length_limit_counts_characters(seeded):
    refused(seeded, "reason_too_long", outcome="rejected", reason="x" * 2001)
    refused(seeded, "reason_too_long", outcome="approved", reason="x" * 2001)
    decide(seeded, sid=3, outcome="rejected", reason="x" * 2000)
    decide(seeded, sid=1, outcome="rejected", reason="\U0001F600" * 2000)
    assert len(seeded.execute("SELECT reason FROM decision WHERE submission_id = 1").fetchone()[0]) == 2000


def test_whitespace_padding_does_not_count_toward_the_limit(seeded):
    decide(seeded, outcome="rejected", reason="  " + "x" * 2000 + "  ")


@pytest.mark.parametrize("reason", [5, b"x", ["x"], {"a": 1}])
def test_non_text_reason_is_a_type_error(seeded, reason):
    with pytest.raises(TypeError):
        decide(seeded, reason=reason)


# ---- outcome allowlist ----

@pytest.mark.parametrize("outcome", ["APPROVED", "approve", "in_review", "new", "", None, ["approved"], 5,
                                     "approved' OR '1'='1", "approved ", "Approved"])
def test_bad_outcome(seeded, outcome):
    refused(seeded, "bad_outcome", outcome=outcome, reason="r")


# ---- locks, second decisions, versions ----

@pytest.mark.parametrize("sid, v", [(6, 1), (7, 2), (8, 1), (13, 1), (14, 1)])
@pytest.mark.parametrize("outcome", OUTCOMES := review.OUTCOMES)
def test_every_decided_item_refuses_every_outcome(seeded, sid, v, outcome):
    refused(seeded, "already_decided", sid=sid, v=v, outcome=outcome, reason="r")


@pytest.mark.parametrize("status", ["approved", "rejected", "changes_requested"])
def test_locked_status_without_a_decision_row(seeded, status):
    seeded.execute("UPDATE submission SET status = ? WHERE id = 3", (status,))
    seeded.commit()
    refused(seeded, "locked", sid=3)


def test_second_decision_keeps_the_first(seeded):
    decide(seeded, outcome="rejected", reason="first", who="Alex Rivera")
    first = state(seeded)
    refused(seeded, "already_decided", outcome="approved", who="Someone Else", now=NOW)
    assert state(seeded) == first


def test_stale_and_missing_versions(seeded):
    refused(seeded, "stale_version", sid=5, v=1, outcome="approved")      # v1 of a 2-version item
    refused(seeded, "stale_version", sid=3, v=2)
    refused(seeded, "stale_version", sid=3, v=0)
    refused(seeded, "stale_version", sid=3, v=-1)
    refused(seeded, "stale_version", sid=3, v="1")
    refused(seeded, "stale_version", sid=3, v=True)
    refused(seeded, "stale_version", sid=3, v=None)


@pytest.mark.parametrize("sid", [9999, 0, -1, "3", None, 3.0, True])
def test_unknown_submission(seeded, sid):
    refused(seeded, "not_found", sid=sid)


# ---- argument guards ----

def test_reviewer_and_clock_guards(seeded):
    for who in ("", "   ", None, 5):
        with pytest.raises(ValueError):
            decide(seeded, who=who)
    before = state(seeded)
    with pytest.raises(TypeError):
        decide(seeded, now="2026-10-02")
    assert state(seeded) == before and not seeded.in_transaction


def test_open_transaction_is_refused(seeded):
    seeded.execute("UPDATE submission SET title = title WHERE id = 1")
    with pytest.raises(RuntimeError):
        decide(seeded)
    seeded.rollback()


# ---- atomicity ----

def test_failing_status_update_leaves_no_decision(seeded, monkeypatch):
    before = state(seeded)

    def boom(*a):
        raise RuntimeError("boom")
    monkeypatch.setattr(review, "_update_status", boom)
    with pytest.raises(RuntimeError):
        decide(seeded)
    assert state(seeded) == before and not seeded.in_transaction


def test_failing_insert_leaves_status_alone(seeded, monkeypatch):
    before = state(seeded)

    def boom(*a):
        raise RuntimeError("boom")
    monkeypatch.setattr(review, "_insert_decision", boom)
    with pytest.raises(RuntimeError):
        decide(seeded)
    assert state(seeded) == before


# ---- backstops and concurrency ----

def test_unique_constraint_is_a_backstop(seeded):
    decide(seeded)
    with pytest.raises(Exception) as e:
        seeded.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
                       " VALUES (3, 1, 'approved', 'x', NULL, '2026-10-02T00:00:00Z')")
    assert "UNIQUE" in str(e.value)
    seeded.rollback()


def test_integrity_error_maps_to_already_decided(seeded, monkeypatch):
    def racing(conn, *a):
        conn.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
                     " VALUES (3, 1, 'approved', 'x', NULL, '2026-10-02T00:00:00Z')")
        raise review.sqlite3.IntegrityError("UNIQUE")
    monkeypatch.setattr(review, "_insert_decision", racing)
    before_decisions = seeded.execute("SELECT count(*) FROM decision").fetchone()[0]
    with pytest.raises(DecisionError) as e:
        decide(seeded)
    assert e.value.code == "already_decided"
    assert seeded.execute("SELECT count(*) FROM decision").fetchone()[0] == before_decisions


@pytest.mark.parametrize("attempt", range(20))
def test_two_threads_one_winner(seeded, db_path, attempt):
    seeded.execute("DELETE FROM decision WHERE submission_id = 6")   # make #6 decidable again for this race
    seeded.execute("UPDATE submission SET status = 'in_review' WHERE id = 6")
    seeded.commit()
    barrier = threading.Barrier(2)
    results = []

    def worker(outcome):
        with db.connect() as c:
            barrier.wait()
            try:
                decide(c, sid=6, outcome=outcome, reason="r", who=ME)
                results.append("ok")
            except DecisionError as e:
                results.append(e.code)
    threads = [threading.Thread(target=worker, args=(o,)) for o in ("approved", "rejected")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["already_decided", "ok"]
    rows = seeded.execute("SELECT outcome FROM decision WHERE submission_id = 6").fetchall()
    status = seeded.execute("SELECT status FROM submission WHERE id = 6").fetchone()[0]
    assert len(rows) == 1 and rows[0][0] == status


# ---- SQL safety ----

def test_sql_in_reason_is_stored_as_text(seeded):
    evil = "'); DROP TABLE decision;--"
    decide(seeded, outcome="rejected", reason=evil)
    assert seeded.execute("SELECT reason FROM decision WHERE submission_id = 3").fetchone()[0] == evil
    assert seeded.execute("SELECT count(*) FROM decision").fetchone()[0] == 8


def test_every_statement_in_review_py_binds_parameters():
    source = Path("app/review.py").read_text()
    assert not re.search(r'f["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)', source, re.I)
    assert not re.search(r'["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)[^"\']*["\']\s*(%|\.format)', source, re.I)
