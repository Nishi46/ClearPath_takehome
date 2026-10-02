import sqlite3

import pytest

from tests.test_schema_submission_version import NOW, add_submission, add_version


@pytest.fixture
def sid(conn):
    s = add_submission(conn)
    add_version(conn, s, 1)
    return s


def decide(conn, sid, version_number=1, **over):
    row = dict(submission_id=sid, version_number=version_number, outcome="approved",
               reviewer="Sam Ortiz", reason=None, created_at=NOW)
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute("INSERT INTO decision (%s) VALUES (%s)" % (cols, marks), list(row.values()))


def comment(conn, sid, version_number=1, **over):
    row = dict(submission_id=sid, version_number=version_number, author="Sam Ortiz",
               text="Please add the APR.", rule_id=None, created_at=NOW)
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute("INSERT INTO comment (%s) VALUES (%s)" % (cols, marks), list(row.values()))


def count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


# decision

def test_valid_decision(conn, sid):
    decide(conn, sid)
    assert count(conn, "decision") == 1


@pytest.mark.parametrize("outcome", ["pending", "APPROVED", "", "approve"])
def test_invalid_outcome_rejected(conn, sid, outcome):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, sid, outcome=outcome, reason="because")


def test_second_decision_on_same_version_rejected(conn, sid):
    decide(conn, sid, outcome="changes_requested", reason="Add APR")
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, sid, outcome="approved")
    assert count(conn, "decision") == 1


def test_decision_on_a_new_version_is_allowed(conn, sid):
    add_version(conn, sid, 2)
    decide(conn, sid, 1, outcome="changes_requested", reason="Add APR")
    decide(conn, sid, 2)


@pytest.mark.parametrize("outcome", ["rejected", "changes_requested"])
@pytest.mark.parametrize("reason", [None, "", "   ", "\t\n "])
def test_negative_outcome_needs_a_reason(conn, sid, outcome, reason):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, sid, outcome=outcome, reason=reason)


@pytest.mark.parametrize("outcome", ["rejected", "changes_requested"])
def test_negative_outcome_with_reason_accepted(conn, sid, outcome):
    decide(conn, sid, outcome=outcome, reason="Misleading claim")


@pytest.mark.parametrize("reason", [None, "", "  "])
def test_approved_needs_no_reason(conn, sid, reason):
    decide(conn, sid, outcome="approved", reason=reason)


def test_decision_on_nonexistent_version_rejected(conn, sid):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, sid, version_number=2)


def test_decision_on_nonexistent_submission_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, 999)


def test_decision_requires_reviewer(conn, sid):
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, sid, reviewer=None)


# comment

def test_valid_comment(conn, sid):
    comment(conn, sid)
    comment(conn, sid, rule_id="R2")
    assert count(conn, "comment") == 2


def test_comment_with_null_rule_id_accepted(conn, sid):
    comment(conn, sid, rule_id=None)
    assert conn.execute("SELECT rule_id FROM comment").fetchone()[0] is None


def test_comment_on_nonexistent_version_rejected(conn, sid):
    with pytest.raises(sqlite3.IntegrityError):
        comment(conn, sid, version_number=2)


def test_comment_on_nonexistent_submission_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        comment(conn, 999)


@pytest.mark.parametrize("text", ["", "   ", "\t\n ", None])
def test_blank_comment_rejected(conn, sid, text):
    with pytest.raises(sqlite3.IntegrityError):
        comment(conn, sid, text=text)


def test_comment_allowed_on_a_decided_version(conn, sid):
    decide(conn, sid)
    comment(conn, sid)  # comments on locked versions are allowed (phase 6)


# cascades and safety

def test_deleting_version_deletes_its_decision_and_comments(conn, sid):
    add_version(conn, sid, 2)
    decide(conn, sid, 1, outcome="rejected", reason="No")
    comment(conn, sid, 1)
    comment(conn, sid, 2)
    conn.execute("DELETE FROM version WHERE submission_id = ? AND version_number = 1", (sid,))
    assert count(conn, "decision") == 0
    assert count(conn, "comment") == 1


def test_deleting_submission_deletes_everything(conn, sid):
    decide(conn, sid)
    comment(conn, sid)
    conn.execute("DELETE FROM submission WHERE id = ?", (sid,))
    for table in ("version", "decision", "comment"):
        assert count(conn, table) == 0


def test_hostile_text_stored_literally(conn, sid):
    evil = "'; DROP TABLE decision; --"
    decide(conn, sid, outcome="rejected", reason=evil)
    comment(conn, sid, text=evil)
    assert conn.execute("SELECT reason FROM decision").fetchone()[0] == evil
    assert conn.execute("SELECT text FROM comment").fetchone()[0] == evil


def test_foreign_key_check_is_clean(conn, sid):
    decide(conn, sid)
    comment(conn, sid)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
