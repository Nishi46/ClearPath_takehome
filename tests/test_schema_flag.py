import sqlite3

import pytest

from tests.test_schema_submission_version import NOW, add_submission, add_version


@pytest.fixture
def version_id(conn):
    sid = add_submission(conn)
    add_version(conn, sid)
    return conn.execute("SELECT id FROM version WHERE submission_id = ?", (sid,)).fetchone()[0]


def add_flag(conn, version_id, **over):
    row = dict(version_id=version_id, rule_id="R1", severity="high", kind="phrase",
               matched_text="guaranteed approval", start_index=10, end_index=29)
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute("INSERT INTO flag (%s) VALUES (%s)" % (cols, marks), list(row.values()))


def add_missing(conn, version_id, **over):
    add_flag(conn, version_id, kind="missing", matched_text=None, start_index=None,
             end_index=None, **over)


def dismiss(conn, version_id, rule_id="R4", note="Not applicable here", **over):
    row = dict(version_id=version_id, rule_id=rule_id, note=note, dismissed_by="Sam Ortiz",
               created_at=NOW)
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute("INSERT INTO flag_dismissal (%s) VALUES (%s)" % (cols, marks), list(row.values()))


def count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def test_valid_phrase_and_missing_flags(conn, version_id):
    add_flag(conn, version_id)
    add_missing(conn, version_id, rule_id="R4", severity="medium")
    assert count(conn, "flag") == 2


def test_phrase_flag_needs_all_three_fields(conn, version_id):
    for missing in ("matched_text", "start_index", "end_index"):
        with pytest.raises(sqlite3.IntegrityError):
            add_flag(conn, version_id, **{missing: None})


def test_missing_flag_must_not_carry_match_fields(conn, version_id):
    for field, value in (("matched_text", "x"), ("start_index", 1), ("end_index", 5)):
        over = dict(kind="missing", matched_text=None, start_index=None, end_index=None)
        over[field] = value
        with pytest.raises(sqlite3.IntegrityError):
            add_flag(conn, version_id, **over)


@pytest.mark.parametrize("start,end", [(5, 5), (9, 3)])
def test_phrase_flag_needs_start_before_end(conn, version_id, start, end):
    with pytest.raises(sqlite3.IntegrityError):
        add_flag(conn, version_id, start_index=start, end_index=end)


@pytest.mark.parametrize("field,value", [
    ("severity", "critical"), ("severity", "HIGH"), ("kind", "regex"), ("kind", ""),
])
def test_invalid_severity_or_kind_rejected(conn, version_id, field, value):
    with pytest.raises(sqlite3.IntegrityError):
        add_flag(conn, version_id, **{field: value})


def test_flag_for_missing_version_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_flag(conn, 999)


@pytest.mark.parametrize("note", ["", "   ", "\t\n "])
def test_blank_dismissal_note_rejected(conn, version_id, note):
    with pytest.raises(sqlite3.IntegrityError):
        dismiss(conn, version_id, note=note)


def test_valid_dismissal(conn, version_id):
    dismiss(conn, version_id)
    assert count(conn, "flag_dismissal") == 1


def test_dismissal_for_missing_version_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        dismiss(conn, 999)


def test_same_rule_cannot_be_dismissed_twice_on_one_version(conn, version_id):
    dismiss(conn, version_id, rule_id="R4")
    with pytest.raises(sqlite3.IntegrityError):
        dismiss(conn, version_id, rule_id="R4")
    dismiss(conn, version_id, rule_id="R5")  # another rule is fine


def test_same_rule_can_be_dismissed_on_different_versions(conn, version_id):
    sid = conn.execute("SELECT submission_id FROM version WHERE id = ?", (version_id,)).fetchone()[0]
    add_version(conn, sid, 2)
    v2 = conn.execute("SELECT id FROM version WHERE version_number = 2").fetchone()[0]
    dismiss(conn, version_id, rule_id="R4")
    dismiss(conn, v2, rule_id="R4")


def test_deleting_version_deletes_flags_and_dismissals(conn, version_id):
    add_flag(conn, version_id)
    add_missing(conn, version_id, rule_id="R4")
    dismiss(conn, version_id)
    conn.execute("DELETE FROM version WHERE id = ?", (version_id,))
    assert count(conn, "flag") == 0
    assert count(conn, "flag_dismissal") == 0


def test_deleting_submission_cascades_through_to_flags(conn, version_id):
    add_flag(conn, version_id)
    dismiss(conn, version_id)
    conn.execute("DELETE FROM submission")
    assert count(conn, "flag") == 0 and count(conn, "flag_dismissal") == 0


def test_hostile_text_stored_literally(conn, version_id):
    evil = "'; DROP TABLE flag; --"
    add_flag(conn, version_id, matched_text=evil)
    dismiss(conn, version_id, note=evil)
    assert conn.execute("SELECT matched_text FROM flag").fetchone()[0] == evil
    assert conn.execute("SELECT note FROM flag_dismissal").fetchone()[0] == evil
