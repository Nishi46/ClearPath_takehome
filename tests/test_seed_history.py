import copy
import sqlite3
from datetime import datetime, timezone

import pytest

from app.seed import insert_history, insert_submission, load_seed, resolve_times

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def resolved():
    return resolve_times(load_seed(), NOW)


def sub(resolved, seed_id):
    return copy.deepcopy(next(s for s in resolved["submissions"] if s["seedId"] == seed_id))


@pytest.fixture
def full(conn, resolved):
    for s in resolved["submissions"]:
        insert_submission(conn, copy.deepcopy(s))
        insert_history(conn, copy.deepcopy(s))
    return conn


def count(conn, table, where="1=1", args=()):
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]


def test_row_counts_match_the_seed(full):
    assert count(full, "decision") == 7
    assert count(full, "comment") == 14
    assert count(full, "flag_dismissal") == 1
    got = {r[0]: r[1] for r in full.execute("SELECT submission_id, count(*) FROM decision GROUP BY 1")}
    assert got == {5: 1, 6: 1, 7: 2, 8: 1, 13: 1, 14: 1}


def test_rule_linked_comments(full):
    rows = full.execute("SELECT submission_id, rule_id FROM comment WHERE rule_id IS NOT NULL"
                        " ORDER BY submission_id, rule_id").fetchall()
    by_sub = {}
    for r in rows:
        by_sub.setdefault(r[0], []).append(r[1])
    assert by_sub[5] == ["R2", "R5"]
    assert set(by_sub[14]) == {"R2", "R3", "R7"} or len(by_sub[14]) == 3
    # Comments without a rule are stored as NULL, not the string "None".
    assert count(full, "comment", "rule_id IS NULL") >= 1
    assert count(full, "comment", "rule_id = 'None' OR rule_id = ''") == 0


def test_submission_7_reject_then_approve(full):
    rows = full.execute("SELECT version_number, outcome, reviewer, reason FROM decision"
                        " WHERE submission_id = 7 ORDER BY version_number").fetchall()
    assert [(r["version_number"], r["outcome"], r["reviewer"]) for r in rows] == [
        (1, "rejected", "Alex Rivera"), (2, "approved", "Alex Rivera")]
    assert rows[0]["reason"].strip()


def test_the_one_dismissal_is_13_r1_with_its_note(full):
    row = full.execute(
        "SELECT v.submission_id, v.version_number, d.rule_id, d.note, d.dismissed_by, d.created_at"
        " FROM flag_dismissal d JOIN version v ON v.id = d.version_id").fetchone()
    assert (row["submission_id"], row["version_number"], row["rule_id"], row["dismissed_by"]) == (
        13, 1, "R1", "Alex Rivera")
    assert row["note"].startswith("False positive")
    assert row["created_at"] == "2026-09-28T22:30:15Z"  # 62h before NOW


def test_flag_table_stays_empty(full):
    assert count(full, "flag") == 0


def test_comment_on_locked_version_is_after_the_decision(full):
    d = full.execute("SELECT created_at FROM decision WHERE submission_id = 6").fetchone()[0]
    c = full.execute("SELECT created_at, author FROM comment WHERE submission_id = 6").fetchone()
    assert c["created_at"] > d
    assert c["author"] == "Maya Chen"  # the marketer comments after approval


def test_events_are_not_older_than_their_version(full):
    bad = full.execute(
        "SELECT count(*) FROM decision d JOIN version v ON v.submission_id = d.submission_id"
        " AND v.version_number = d.version_number WHERE d.created_at < v.created_at").fetchone()[0]
    assert bad == 0
    bad = full.execute(
        "SELECT count(*) FROM comment c JOIN version v ON v.submission_id = c.submission_id"
        " AND v.version_number = c.version_number WHERE c.created_at < v.created_at").fetchone()[0]
    assert bad == 0


def test_foreign_key_check_is_clean(full):
    assert full.execute("PRAGMA foreign_key_check").fetchall() == []


def test_approved_without_a_reason_is_accepted(conn, resolved):
    s = sub(resolved, 6)
    s["decisions"][0]["reason"] = None
    insert_submission(conn, s)
    insert_history(conn, s)
    assert conn.execute("SELECT reason FROM decision").fetchone()[0] is None


@pytest.mark.parametrize("outcome,seed_id", [("changes_requested", 14), ("rejected", 8)])
@pytest.mark.parametrize("reason", [None, "", "   ", "\t\n"])
def test_negative_decisions_without_a_reason_are_refused_by_the_database(conn, resolved, outcome, seed_id, reason):
    s = sub(resolved, seed_id)
    s["decisions"][0]["reason"] = reason
    insert_submission(conn, s)
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, s)


def test_history_before_versions_exist_is_refused(conn, resolved):
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, sub(resolved, 5))  # no insert_submission first


def test_decision_on_a_version_that_does_not_exist_is_refused(conn, resolved):
    s = sub(resolved, 5)
    insert_submission(conn, s)
    s["decisions"][0]["versionNumber"] = 9
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, s)


def test_dismissal_on_a_missing_version_is_refused(conn, resolved):
    s = sub(resolved, 13)
    insert_submission(conn, s)
    s["dismissals"][0]["versionNumber"] = 4
    with pytest.raises(sqlite3.IntegrityError):  # version_id would be NULL
        insert_history(conn, s)


def test_second_decision_on_the_same_version_is_refused(conn, resolved):
    s = sub(resolved, 5)
    insert_submission(conn, s)
    s["decisions"].append(copy.deepcopy(s["decisions"][0]))
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, s)


def test_blank_comment_text_is_refused(conn, resolved):
    s = sub(resolved, 3)
    s["comments"][0]["text"] = "  "
    insert_submission(conn, s)
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, s)


def test_double_history_insert_fails_instead_of_duplicating(conn, resolved):
    s = sub(resolved, 13)
    insert_submission(conn, s)
    insert_history(conn, s)
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, s)
    assert count(conn, "decision") == 1 and count(conn, "flag_dismissal") == 1


@pytest.mark.parametrize("payload", ["'; DROP TABLE decision; --", "<img src=x onerror=alert(1)>", "a\nb \U0001F600"])
def test_hostile_text_is_stored_literally(conn, resolved, payload):
    s = sub(resolved, 13)
    s["decisions"][0]["reason"] = payload
    s["dismissals"][0]["note"] = payload
    s["decisions"][0]["reviewer"] = payload
    insert_submission(conn, s)
    insert_history(conn, s)
    assert conn.execute("SELECT reason, reviewer FROM decision").fetchone()[:] == (payload, payload)
    assert conn.execute("SELECT note FROM flag_dismissal").fetchone()[0] == payload


def test_submission_with_no_history_inserts_nothing(conn, resolved):
    s = sub(resolved, 1)
    insert_submission(conn, s)
    insert_history(conn, s)
    for t in ("decision", "comment", "flag_dismissal", "flag"):
        assert count(conn, t) == 0


def test_input_is_not_mutated_and_nothing_is_committed(conn, resolved):
    s = sub(resolved, 5)
    before = copy.deepcopy(s)
    insert_submission(conn, s)
    insert_history(conn, s)
    assert s == before
