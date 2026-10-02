import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app import db, flags, review, rules, seed, submit
from app.queue import list_queue
from app.submit import SubmitError, create_version

NOW = datetime(2026, 10, 7, 12, 30, 5, tzinfo=timezone.utc)
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")


@pytest.fixture
def seeded(client):
    with db.connect() as c:
        yield c


def dump(conn):
    return {t: [tuple(r) for r in conn.execute("SELECT * FROM %s ORDER BY id" % t)] for t in TABLES}


def current_copy(conn, sid):
    return conn.execute("SELECT v.copy FROM version v JOIN submission s ON s.id = v.submission_id"
                        " AND v.version_number = s.current_version WHERE s.id = ?", (sid,)).fetchone()[0]


def fields(copy="Apply today. Rates from 5.99% APR. Subject to credit approval.", **over):
    base = {"copy": copy, "notes": "Fixed per feedback.", "launch_date": "2026-11-20"}
    base.update(over)
    return base


def resubmit(conn, sid=14, base=1, who="Jordan Lee", **over):
    return create_version(conn, sid, base, fields(**over), who, NOW)


def v1_rows(conn, sid):
    """Everything that belongs to version 1 of a submission, as plain tuples."""
    out = {}
    out["version"] = [tuple(r) for r in conn.execute("SELECT * FROM version WHERE submission_id = ? AND version_number = 1", (sid,))]
    out["flags"] = [tuple(r) for r in conn.execute(
        "SELECT f.* FROM flag f JOIN version v ON v.id = f.version_id WHERE v.submission_id = ? AND v.version_number = 1 ORDER BY f.id", (sid,))]
    out["decision"] = [tuple(r) for r in conn.execute("SELECT * FROM decision WHERE submission_id = ? AND version_number = 1", (sid,))]
    out["comments"] = [tuple(r) for r in conn.execute("SELECT * FROM comment WHERE submission_id = ? AND version_number = 1 ORDER BY id", (sid,))]
    out["dismissals"] = [tuple(r) for r in conn.execute(
        "SELECT d.* FROM flag_dismissal d JOIN version v ON v.id = d.version_id WHERE v.submission_id = ? AND v.version_number = 1", (sid,))]
    return out


# ---- success ----

def test_resubmitting_14_adds_v2_and_leaves_v1_untouched(seeded):
    before = v1_rows(seeded, 14)
    assert resubmit(seeded) == 2
    s = seeded.execute("SELECT status, current_version, launch_date FROM submission WHERE id = 14").fetchone()
    assert tuple(s) == ("in_review", 2, "2026-11-20")
    assert v1_rows(seeded, 14) == before
    v2 = seeded.execute("SELECT * FROM version WHERE submission_id = 14 AND version_number = 2").fetchone()
    assert (v2["copy"], v2["notes"], v2["created_at"]) == (fields()["copy"], "Fixed per feedback.", "2026-10-07T12:30:05Z")
    stored = [(r["rule_id"], r["severity"], r["kind"], r["matched_text"], r["start_index"], r["end_index"])
              for r in seeded.execute("SELECT * FROM flag WHERE version_id = ? ORDER BY rule_id, id", (v2["id"],))]
    expected = sorted((f.rule_id, f.severity, f.kind, f.matched_text, f.start, f.end)
                      for f in rules.evaluate("mortgage", "display", fields()["copy"]))
    assert stored == expected
    assert not seeded.in_transaction


def test_the_queue_shows_the_resubmission(seeded):
    resubmit(seeded)
    row = next(r for r in list_queue(seeded) if r["id"] == 14)
    assert (row["status"], row["version_number"], row["launch_date"]) == ("in_review", 2, "2026-11-20")
    assert row["flag_count"] == len({f.rule_id for f in rules.evaluate("mortgage", "display", fields()["copy"])})


def test_a_rejected_item_can_be_resubmitted_and_keeps_its_history(seeded):
    before = v1_rows(seeded, 8)
    assert resubmit(seeded, 8, 1, copy="A calm, compliant rewrite of the whole page.") == 2
    assert v1_rows(seeded, 8) == before
    assert [r[0] for r in seeded.execute("SELECT outcome FROM decision WHERE submission_id = 8")] == ["rejected"]
    assert seeded.execute("SELECT status FROM submission WHERE id = 8").fetchone()[0] == "in_review"


def test_only_copy_notes_and_launch_date_can_change(seeded):
    before = tuple(seeded.execute("SELECT title, product, channel, submitted_by, created_at FROM submission WHERE id = 14").fetchone())
    resubmit(seeded, title="Hacked", product="loan", channel="email", submitted_by="Maya Chen", status="approved",
             current_version=9, created_at="1999-01-01T00:00:00Z", id=1)
    after = tuple(seeded.execute("SELECT title, product, channel, submitted_by, created_at FROM submission WHERE id = 14").fetchone())
    assert after == before


def test_the_stored_copy_is_normalized_and_blank_notes_are_null(seeded):
    resubmit(seeded, copy="\n  New line one\r\nNew line two \r\n", notes="  ")
    row = seeded.execute("SELECT copy, notes FROM version WHERE submission_id = 14 AND version_number = 2").fetchone()
    assert tuple(row) == ("New line one\nNew line two", None)


def test_other_submissions_are_untouched(seeded):
    before = dump(seeded)
    resubmit(seeded)
    after = dump(seeded)
    for t in ("decision", "comment", "flag_dismissal"):
        assert after[t] == before[t]
    assert [r for r in after["submission"] if r[0] != 14] == [r for r in before["submission"] if r[0] != 14]


# ---- refusals: each one writes nothing ----

def refused(conn, code, *args, **kw):
    before = dump(conn)
    with pytest.raises(SubmitError) as err:
        resubmit(conn, *args, **kw)
    assert err.value.code == code
    assert dump(conn) == before and not conn.in_transaction


@pytest.mark.parametrize("sid", [6, 7, 13])
def test_approved_items_are_locked(seeded, sid):
    refused(seeded, "not_resubmittable", sid, seeded.execute("SELECT current_version FROM submission WHERE id = ?", (sid,)).fetchone()[0],
            who=seeded.execute("SELECT submitted_by FROM submission WHERE id = ?", (sid,)).fetchone()[0])


@pytest.mark.parametrize("sid,who", [(1, "Maya Chen"), (9, "Maya Chen"), (5, "Maya Chen"), (4, "Jordan Lee")])
def test_items_waiting_for_the_reviewer_cannot_be_resubmitted(seeded, sid, who):
    base = seeded.execute("SELECT current_version FROM submission WHERE id = ?", (sid,)).fetchone()[0]
    refused(seeded, "not_resubmittable", sid, base, who=who)


def test_changes_requested_without_a_decision_row_is_refused(seeded):
    seeded.execute("UPDATE submission SET status = 'changes_requested' WHERE id = 9")
    seeded.commit()
    refused(seeded, "not_resubmittable", 9, 1, who="Maya Chen")


def test_a_different_marketer_is_refused(seeded):
    refused(seeded, "not_owner", 14, 1, who="Maya Chen")


@pytest.mark.parametrize("who", ["", None, "jordan lee", "Jordan Lee ", "Mallory", ["Jordan Lee"]])
def test_only_the_exact_submitter_may_resubmit(seeded, who):
    refused(seeded, "not_owner", 14, 1, who=who)


@pytest.mark.parametrize("sid", [9999, 0, -1, None, "14", 14.0, True, [14]])
def test_unknown_submissions_are_not_found(seeded, sid):
    refused(seeded, "not_found", sid, 1)


@pytest.mark.parametrize("base", [0, 2, -1, "1", None, 1.0, True, [1], 10**12])
def test_a_base_version_that_is_not_the_current_one_is_stale(seeded, base):
    refused(seeded, "stale_version", 14, base)


def test_the_check_order_is_fixed(seeded):
    refused(seeded, "not_found", 9999, 7, who="Mallory")                                         # not found beats owner
    refused(seeded, "not_owner", 14, 7, who="Maya Chen")                                         # owner beats stale
    refused(seeded, "stale_version", 6, 9, who="Maya Chen")                                      # stale beats locked


@pytest.mark.parametrize("copy", [
    None,  # filled in below: the exact stored copy
    "STORED\r\n", "  STORED  ", "\n\nSTORED\n",
])
def test_unchanged_copy_is_refused_however_it_is_written(seeded, copy):
    stored = current_copy(seeded, 14)
    sent = stored if copy is None else copy.replace("STORED", stored)
    refused(seeded, "unchanged", 14, 1, copy=sent)


def test_crlf_in_the_stored_copy_does_not_hide_an_unchanged_resubmission(seeded):
    seeded.execute("UPDATE version SET copy = replace(copy, char(10), char(13) || char(10)) WHERE submission_id = 14")
    seeded.commit()
    refused(seeded, "unchanged", 14, 1, copy=current_copy(seeded, 14).replace("\r\n", "\n"))


def test_a_notes_only_or_date_only_change_is_still_unchanged(seeded):
    stored = current_copy(seeded, 14)
    refused(seeded, "unchanged", 14, 1, copy=stored, notes="Completely different notes.")
    refused(seeded, "unchanged", 14, 1, copy=stored, launch_date="2027-01-01")


def test_a_whitespace_change_inside_a_line_is_a_change(seeded):
    stored = current_copy(seeded, 14)
    assert resubmit(seeded, copy=stored.replace(" ", "  ", 1)) == 2


def test_unvalidated_fields_are_refused_before_the_lock(seeded):
    for bad in (fields(copy="   "), fields(copy="a\x00b"), fields(launch_date="2026-02-30"), fields(notes="x" * 2001),
                {"copy": "ok"}, {}):
        with pytest.raises(ValueError):
            create_version(seeded, 14, 1, bad, "Jordan Lee", NOW)
    assert not seeded.in_transaction


def test_it_refuses_a_connection_that_is_already_in_a_transaction(seeded):
    seeded.execute("BEGIN")
    with pytest.raises(RuntimeError):
        resubmit(seeded)
    seeded.rollback()


# ---- the version cap ----

def _stack_versions(conn, sid, top):
    """Give a changes-requested submission versions 2..top, with a decision on the top one."""
    for n in range(2, top + 1):
        conn.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                     " VALUES (?, ?, ?, NULL, '2026-10-01T00:00:00Z')", (sid, n, "Copy number %d." % n))
    conn.execute("UPDATE submission SET current_version = ?, status = 'changes_requested' WHERE id = ?", (top, sid))
    conn.execute("DELETE FROM decision WHERE submission_id = ? AND version_number = ?", (sid, top))
    conn.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
                 " VALUES (?, ?, 'changes_requested', 'Alex Rivera', 'Again.', '2026-10-02T00:00:00Z')", (sid, top))
    conn.commit()


def test_the_tenth_version_is_allowed_and_the_eleventh_is_not(seeded):
    _stack_versions(seeded, 14, submit.MAX_VERSIONS - 1)
    assert resubmit(seeded, 14, submit.MAX_VERSIONS - 1) == submit.MAX_VERSIONS
    seeded.execute("DELETE FROM decision WHERE submission_id = 14 AND version_number = ?", (submit.MAX_VERSIONS,))
    seeded.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
                   " VALUES (14, ?, 'rejected', 'Alex Rivera', 'No.', '2026-10-03T00:00:00Z')", (submit.MAX_VERSIONS,))
    seeded.execute("UPDATE submission SET status = 'rejected' WHERE id = 14")
    seeded.commit()
    refused(seeded, "capacity", 14, submit.MAX_VERSIONS, copy="Yet another different copy.")


# ---- the rules around it ----

def test_a_second_resubmit_of_the_same_base_is_stale(seeded):
    resubmit(seeded)
    refused(seeded, "stale_version", 14, 1, copy="A third, different copy.")


def test_the_unique_constraint_is_the_backstop(seeded):
    resubmit(seeded)
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                       " VALUES (14, 2, 'x', NULL, '2026-10-07T00:00:00Z')")
    seeded.rollback()


def test_twenty_concurrent_resubmits_add_exactly_one_version(client):
    def attempt(i):
        with db.connect() as c:
            try:
                return create_version(c, 14, 1, fields(copy="Different copy number %d." % i), "Jordan Lee", NOW)
            except SubmitError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count(2) == 1 and results.count("stale_version") == 19
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM version WHERE submission_id = 14").fetchone()[0] == 2
        assert c.execute("SELECT current_version FROM submission WHERE id = 14").fetchone()[0] == 2


def test_a_resubmit_racing_a_decision_on_v1_never_leaves_a_decision_after_the_new_version(client):
    def resubmit_job():
        with db.connect() as c:
            try:
                return create_version(c, 14, 1, fields(copy="Racing copy."), "Jordan Lee", NOW)
            except SubmitError as exc:
                return exc.code

    def decide_job():
        with db.connect() as c:
            try:
                return review.record_decision(c, 14, 1, "approved", None, "Alex Rivera", NOW)["outcome"]
            except review.DecisionError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(resubmit_job), pool.submit(decide_job)
        results = (a.result(), b.result())
    assert results[0] == 2 and results[1] in ("already_decided", "stale_version")
    with db.connect() as c:
        assert [tuple(r) for r in c.execute("SELECT version_number, outcome FROM decision WHERE submission_id = 14")] == \
            [(1, "changes_requested")]


# ---- atomicity ----

def state(conn):
    return dump(conn)


def test_a_failure_while_storing_flags_changes_nothing(seeded, monkeypatch):
    before = state(seeded)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(flags, "store_flags", boom)
    with pytest.raises(RuntimeError):
        resubmit(seeded)
    assert state(seeded) == before and not seeded.in_transaction


@pytest.mark.parametrize("trigger", [
    "CREATE TRIGGER t BEFORE INSERT ON version BEGIN SELECT RAISE(ABORT, 'no'); END",
    "CREATE TRIGGER t BEFORE INSERT ON flag BEGIN SELECT RAISE(ABORT, 'no'); END",
    "CREATE TRIGGER t BEFORE UPDATE ON submission BEGIN SELECT RAISE(ABORT, 'no'); END",
])
def test_a_failure_at_any_write_changes_nothing(seeded, trigger):
    # The version is inserted first, then its flags, then the submission is updated: fail each in turn.
    seeded.execute(trigger)
    seeded.commit()
    before = state(seeded)
    with pytest.raises(sqlite3.DatabaseError):
        resubmit(seeded)
    assert state(seeded) == before and not seeded.in_transaction


# ---- it fits with the decision rules ----

def test_after_a_resubmit_a_late_decision_on_v1_is_refused_and_v2_can_be_decided(seeded):
    resubmit(seeded)
    with pytest.raises(review.DecisionError) as err:
        review.record_decision(seeded, 14, 1, "approved", None, "Alex Rivera", NOW)
    assert err.value.code == "stale_version"
    review.record_decision(seeded, 14, 2, "approved", None, "Alex Rivera", NOW)
    assert seeded.execute("SELECT status FROM submission WHERE id = 14").fetchone()[0] == "approved"
    refused(seeded, "not_resubmittable", 14, 2, copy="Edit after approval.")


def test_the_loop_works_more_than_once(seeded):
    assert resubmit(seeded, copy="Second copy.") == 2
    review.record_decision(seeded, 14, 2, "changes_requested", "Still wrong.", "Alex Rivera", NOW)
    assert resubmit(seeded, 14, 2, copy="Third copy.") == 3
    review.record_decision(seeded, 14, 3, "rejected", "No.", "Alex Rivera", NOW)
    assert resubmit(seeded, 14, 3, copy="Fourth copy.") == 4
    assert [r[0] for r in seeded.execute("SELECT outcome FROM decision WHERE submission_id = 14 ORDER BY version_number")] == \
        ["changes_requested", "changes_requested", "rejected"]
    assert seeded.execute("SELECT count(*) FROM version WHERE submission_id = 14").fetchone()[0] == 4


# ---- reset and SQL safety ----

def test_reset_after_resubmissions_equals_a_fresh_seed(conn):
    seed.seed_all(conn, NOW)
    fresh = dump(conn)
    create_version(conn, 14, 1, fields(), "Jordan Lee", NOW)
    create_version(conn, 8, 1, fields(copy="Rewrite."), "Jordan Lee", NOW)
    assert dump(conn) != fresh
    seed.reset_to_seed(conn, NOW)
    assert dump(conn) == fresh
    assert create_version(conn, 14, 1, fields(), "Jordan Lee", NOW) == 2       # and it works again


def test_injection_text_is_stored_verbatim(seeded):
    evil = "'); DROP TABLE version;--"
    resubmit(seeded, copy=evil, notes="'; DELETE FROM decision; --")
    assert seeded.execute("SELECT copy FROM version WHERE submission_id = 14 AND version_number = 2").fetchone()[0] == evil
    assert seeded.execute("SELECT count(*) FROM decision").fetchone()[0] > 0


def test_every_statement_in_the_module_uses_placeholders():
    source = open(submit.__file__).read()
    assert not re.search(r'f["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)', source, re.I)
    assert not re.search(r'["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)[^"\']*["\']\s*(%|\.format)', source, re.I)
