import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, review
from app.review import DismissError, dismiss_flag, record_decision
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 2, 9, 5, 0, tzinfo=timezone.utc)
ME = "Alex Rivera"
NOTE = "False positive: the phrase is quoted."


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def rows(c):
    return tuple(tuple(tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t))
                 for t in ("submission", "version", "flag", "flag_dismissal", "decision", "comment"))


def dismiss(c, sid=12, v=1, rule="R4", note=NOTE, who=ME, now=LATER):
    return dismiss_flag(c, sid, v, rule, note, who, now)


def refused(c, code, *a, **k):
    before = rows(c)
    with pytest.raises(DismissError) as e:
        dismiss(c, *a, **k)
    assert e.value.code == code
    assert rows(c) == before
    assert not c.in_transaction


def test_happy_path_on_12(seeded):
    before = rows(seeded)
    out = dismiss(seeded, note="  " + NOTE + "  ")
    after = rows(seeded)
    assert after[:3] == before[:3] and after[4:] == before[4:]  # flags, status, decisions untouched
    assert len(after[3]) == len(before[3]) + 1
    new = seeded.execute("SELECT d.*, v.submission_id, v.version_number FROM flag_dismissal d JOIN version v"
                         " ON v.id = d.version_id WHERE d.id = (SELECT max(id) FROM flag_dismissal)").fetchone()
    assert (new["submission_id"], new["version_number"], new["rule_id"]) == (12, 1, "R4")
    assert (new["note"], new["dismissed_by"], new["created_at"]) == (NOTE, ME, "2026-10-02T09:05:00Z")
    assert out["created_at"] == "2026-10-02T09:05:00Z" and out["note"] == NOTE
    assert not seeded.in_transaction


def test_queue_drops_the_flag_after_dismissal(seeded):
    from app.queue import list_queue

    def flags_of(sid):
        row = next(r for r in list_queue(seeded) if r["id"] == sid)
        return {k: v for k, v in dict(row).items() if "flag" in k or "sever" in k}

    assert any(v for v in flags_of(12).values())
    dismiss(seeded)
    assert not any(v for v in flags_of(12).values()), flags_of(12)


@pytest.mark.parametrize("sid, rule", [(13, "R1"), (6, "R1"), (7, "R1"), (8, "R1"), (14, "R2")])
def test_decided_items_are_refused(seeded, sid, rule):
    v = seeded.execute("SELECT current_version FROM submission WHERE id = ?", (sid,)).fetchone()[0]
    refused(seeded, "already_decided", sid, v, rule)


def test_locked_fallback_without_a_decision_row(seeded):
    seeded.execute("UPDATE submission SET status = 'approved' WHERE id = 12")
    seeded.commit()
    refused(seeded, "locked")


@pytest.mark.parametrize("v", [1, 0, -1, 99, None, True, 1.0, "2"])
def test_stale_versions_on_5(seeded, v):
    refused(seeded, "stale_version", 5, v, "R1")


@pytest.mark.parametrize("sid", [0, 999, None, "12", True, 12.0])
def test_unknown_submission(seeded, sid):
    refused(seeded, "not_found", sid)


@pytest.mark.parametrize("rule", ["R2", "R99", "", None, "r4", "R4 ", ["R4"], "R4' OR '1'='1", b"R4", 4])
def test_no_such_flag(seeded, rule):
    refused(seeded, "no_such_flag", 12, 1, rule)


def test_rule_that_fired_on_another_item_is_refused(seeded):
    seeded.execute("DELETE FROM decision WHERE submission_id = 14")
    seeded.execute("UPDATE submission SET status = 'in_review' WHERE id = 14")
    seeded.commit()
    refused(seeded, "no_such_flag", 14, 1, "R4")
    assert dismiss(seeded, 14, 1, "R2")["rule_id"] == "R2"  # a missing-text rule that fired is dismissable


def test_second_dismissal_keeps_the_first(seeded):
    dismiss(seeded)
    first = rows(seeded)[3]
    refused(seeded, "already_dismissed", note="another", who="Someone Else", now=LATER.replace(hour=23))
    assert rows(seeded)[3] == first
    with pytest.raises(Exception) as e:  # the UNIQUE constraint is the backstop
        seeded.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                       " SELECT version_id, rule_id, 'x', 'y', 'z' FROM flag_dismissal WHERE rule_id = 'R4'")
    assert "UNIQUE" in str(e.value)
    seeded.rollback()


@pytest.mark.parametrize("note, code", [("", "note_required"), ("   ", "note_required"), (None, "note_required"),
                                        ("​", "note_required"), (["x"], "note_required"),
                                        ("x" * 1001, "note_too_long"), ("a\x00b", "note_bad_chars")])
def test_note_rules_write_nothing(seeded, note, code):
    refused(seeded, code, note=note)


def test_note_boundaries_and_crlf(seeded):
    assert dismiss(seeded, note="x" * 1000)["note"] == "x" * 1000


def test_crlf_is_normalized_in_storage(seeded):
    assert dismiss(seeded, note="a\r\nb")["note"] == "a\nb"


def test_note_with_sql_is_stored_verbatim(seeded):
    evil = "'); DROP TABLE flag_dismissal;--"
    dismiss(seeded, note=evil)
    assert seeded.execute("SELECT note FROM flag_dismissal WHERE rule_id = 'R4'").fetchone()[0] == evil
    assert seeded.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 2


@pytest.mark.parametrize("who", ["", "   ", None, 5])
def test_reviewer_must_be_a_name(seeded, who):
    before = rows(seeded)
    with pytest.raises(ValueError):
        dismiss(seeded, who=who)
    assert rows(seeded) == before and not seeded.in_transaction


def test_open_transaction_is_refused(seeded):
    seeded.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError):
        dismiss(seeded)
    seeded.rollback()


def test_failure_in_the_insert_rolls_back(seeded, monkeypatch):
    from app import seed

    before = rows(seeded)

    def boom(_now):
        raise RuntimeError("boom")

    monkeypatch.setattr(seed, "format_timestamp", boom)
    with pytest.raises(RuntimeError):
        dismiss(seeded)
    assert rows(seeded) == before and not seeded.in_transaction


def test_commit_failure_leaves_nothing(db_path, monkeypatch):
    schema = (Path(db.__file__).parent / "schema.sql").read_text()
    with db.connect() as c:
        c.executescript(schema)
        seed_all(c, NOW)
    class Boom:
        """Wraps the connection so the commit fails after the insert succeeded."""

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self):
            raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        with db.connect() as c:  # the error leaves the block, as in a route, so the connection rolls back
            dismiss(Boom(c))
    with db.connect() as fresh:
        assert fresh.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 1


def _threads(n, fn):
    results, errors = [], []

    def run(i):
        try:
            with db.connect() as c:
                results.append(fn(c, i))
        except DismissError as e:
            results.append(e.code)
        except Exception as e:  # pragma: no cover - would fail the assertions below
            errors.append(repr(e))

    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    return results


def test_concurrent_dismissals_make_one_row(db_path, seeded):
    seeded.commit()
    out = _threads(20, lambda c, i: dismiss(c, note="n%d" % i) and "ok")
    assert sorted(out) == ["already_dismissed"] * 19 + ["ok"]
    assert seeded.execute("SELECT count(*) FROM flag_dismissal WHERE rule_id = 'R4'").fetchone()[0] == 1


@pytest.mark.parametrize("round_", range(20))
def test_race_with_a_decision_never_dismisses_after_it(db_path, conn, round_):
    seed_all(conn, NOW)
    conn.commit()

    def act(c, i):
        if i == 0:
            return record_decision(c, 12, 1, "approved", None, ME, datetime.now(timezone.utc)) and "decided"
        return dismiss(c, now=datetime.now(timezone.utc)) and "dismissed"

    out = _threads(2, act)
    d = conn.execute("SELECT created_at FROM decision WHERE submission_id = 12").fetchone()
    x = conn.execute("SELECT created_at FROM flag_dismissal WHERE rule_id = 'R4' AND version_id ="
                     " (SELECT id FROM version WHERE submission_id = 12)").fetchone()
    assert d is not None
    if x is not None:
        assert "dismissed" in out and x["created_at"] <= d["created_at"]
        # the dismissal committed first, so the decision still went through on the same version
    else:
        assert "already_decided" in out


def test_every_statement_uses_placeholders_and_audit_rows_are_never_changed():
    src = Path(review.__file__).read_text()
    body = src[src.index("def dismiss_flag"):src.index("DISMISS_STATUS")]
    for stmt in re.findall(r'"((?:SELECT|INSERT|UPDATE|DELETE)[^"]*)"', body):
        assert "%" not in stmt and "format" not in stmt
    assert not re.search(r"(UPDATE|DELETE FROM)\s+flag_dismissal", src)
    assert not re.search(r"(?i)(execute|\")\s*\w*\s*f\"", body)
