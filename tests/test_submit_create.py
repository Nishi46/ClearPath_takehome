import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app import db, flags, rules, seed, submit
from app.queue import list_queue, row_view
from app.submit import SubmitError, create_submission

NOW = datetime(2026, 10, 7, 12, 30, 5, tzinfo=timezone.utc)


def fields(**over):
    base = {"title": "Spring promo", "product": "loan", "channel": "email", "launch_date": "2026-11-20",
            "copy": "Guaranteed approval, rates as low as 5.99%.", "notes": "First draft."}
    base.update(over)
    return base


def counts(conn):
    return [conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
            for t in ("submission", "version", "flag", "decision", "comment")]


def test_happy_path(conn):
    sid = create_submission(conn, fields(), "Maya Chen", NOW)
    s = conn.execute("SELECT * FROM submission WHERE id = ?", (sid,)).fetchone()
    assert (s["title"], s["product"], s["channel"], s["status"], s["launch_date"], s["submitted_by"],
            s["current_version"], s["created_at"]) == ("Spring promo", "loan", "email", "new", "2026-11-20",
                                                      "Maya Chen", 1, "2026-10-07T12:30:05Z")
    v = conn.execute("SELECT * FROM version WHERE submission_id = ?", (sid,)).fetchall()
    assert len(v) == 1 and v[0]["version_number"] == 1 and v[0]["notes"] == "First draft."
    assert v[0]["created_at"] == "2026-10-07T12:30:05Z"
    stored = [(r["rule_id"], r["severity"], r["kind"], r["matched_text"], r["start_index"], r["end_index"])
              for r in conn.execute("SELECT * FROM flag WHERE version_id = ? ORDER BY rule_id, id", (v[0]["id"],))]
    expected = [(f.rule_id, f.severity, f.kind, f.matched_text, f.start, f.end)
                for f in rules.evaluate("loan", "email", fields()["copy"])]
    assert stored == sorted(expected) and stored
    assert not conn.in_transaction


def test_blank_notes_are_stored_as_null(conn):
    sid = create_submission(conn, fields(notes="  "), "Maya Chen", NOW)
    assert conn.execute("SELECT notes FROM version WHERE submission_id = ?", (sid,)).fetchone()[0] is None


def test_the_stored_copy_is_the_normalized_copy(conn):
    sid = create_submission(conn, fields(copy="\n Line one\r\nLine two \n"), "Maya Chen", NOW)
    assert conn.execute("SELECT copy FROM version WHERE submission_id = ?", (sid,)).fetchone()[0] == "Line one\nLine two"


def test_the_caller_cannot_choose_status_version_or_time(conn):
    sid = create_submission(conn, fields(status="approved", current_version=7, created_at="1999-01-01T00:00:00Z",
                                         submitted_by="Mallory", id=1), "Maya Chen", NOW)
    s = conn.execute("SELECT status, current_version, created_at, submitted_by, id FROM submission").fetchone()
    assert tuple(s) == ("new", 1, "2026-10-07T12:30:05Z", "Maya Chen", sid)


def test_unvalidated_fields_are_refused_before_any_write(conn):
    for bad in (fields(title="  "), fields(product="credit"), fields(launch_date="2026-02-30"), fields(copy="​")):
        with pytest.raises(ValueError):
            create_submission(conn, bad, "Maya Chen", NOW)
    assert counts(conn) == [0] * 5


def test_duplicate_is_refused_with_the_existing_id(conn):
    first = create_submission(conn, fields(), "Maya Chen", NOW)
    with pytest.raises(SubmitError) as err:
        create_submission(conn, fields(notes="different notes", launch_date="2026-12-01"), "Maya Chen", NOW)
    assert err.value.code == "duplicate" and err.value.existing_id == first
    assert counts(conn)[:2] == [1, 1]


def test_duplicate_is_matched_on_normalized_copy(conn):
    create_submission(conn, fields(copy="One\nTwo"), "Maya Chen", NOW)
    with pytest.raises(SubmitError):
        create_submission(conn, fields(copy="One\r\nTwo\r\n"), "Maya Chen", NOW)


@pytest.mark.parametrize("change", [{"title": "Other"}, {"product": "card"}, {"channel": "display"},
                                    {"copy": "Different copy."}])
def test_a_difference_in_any_field_is_not_a_duplicate(conn, change):
    create_submission(conn, fields(), "Maya Chen", NOW)
    create_submission(conn, fields(**change), "Maya Chen", NOW)
    assert counts(conn)[0] == 2


def test_another_marketer_can_submit_the_same_thing(conn):
    create_submission(conn, fields(), "Maya Chen", NOW)
    create_submission(conn, fields(), "Jordan Lee", NOW)
    assert counts(conn)[0] == 2


def _fill(conn, n):
    conn.executemany(
        "INSERT INTO submission (title, product, channel, status, launch_date, submitted_by, created_at,"
        " current_version) VALUES (?, 'loan', 'email', 'new', '2026-11-20', 'Maya Chen', '2026-10-01T00:00:00Z', 1)",
        [("Filler %d" % i,) for i in range(n)])
    conn.commit()


def test_capacity_boundary(conn):
    _fill(conn, submit.MAX_SUBMISSIONS - 1)
    create_submission(conn, fields(), "Maya Chen", NOW)          # the 300th is allowed
    before = counts(conn)
    with pytest.raises(SubmitError) as err:
        create_submission(conn, fields(title="One too many"), "Maya Chen", NOW)
    assert err.value.code == "capacity" and counts(conn) == before


@pytest.mark.parametrize("who", ["Mallory", "", None, "Maya Chen ", "maya chen", ["Maya Chen"]])
def test_only_known_marketers_can_submit(conn, who):
    with pytest.raises(SubmitError) as err:
        create_submission(conn, fields(), who, NOW)
    assert err.value.code == "bad_marketer" and counts(conn) == [0] * 5


def test_a_failure_while_storing_flags_leaves_nothing(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(flags, "store_flags", boom)
    with pytest.raises(RuntimeError):
        create_submission(conn, fields(), "Maya Chen", NOW)
    assert counts(conn) == [0] * 5 and not conn.in_transaction


def test_a_failure_while_inserting_the_version_leaves_nothing(conn):
    conn.execute("CREATE TRIGGER no_versions BEFORE INSERT ON version BEGIN SELECT RAISE(ABORT, 'no'); END")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        create_submission(conn, fields(), "Maya Chen", NOW)
    assert counts(conn) == [0] * 5


def test_a_failure_while_inserting_a_flag_keeps_the_rows_consistent(conn):
    conn.execute("CREATE TRIGGER no_flags BEFORE INSERT ON flag BEGIN SELECT RAISE(ABORT, 'no'); END")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        create_submission(conn, fields(), "Maya Chen", NOW)
    assert counts(conn) == [0] * 5


def test_it_refuses_a_connection_that_is_already_in_a_transaction(conn):
    conn.execute("BEGIN")
    with pytest.raises(RuntimeError):
        create_submission(conn, fields(), "Maya Chen", NOW)
    conn.rollback()


def test_twenty_identical_requests_make_one_submission(db_path):
    db.init_schema()

    def attempt(_):
        with db.connect() as c:
            try:
                return create_submission(c, fields(), "Maya Chen", NOW)
            except SubmitError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(isinstance(r, int) for r in results) == 1
    assert results.count("duplicate") == 19
    with db.connect() as c:
        assert counts(c)[:2] == [1, 1]


def test_twenty_different_requests_all_succeed_with_distinct_ids(db_path):
    db.init_schema()

    def attempt(i):
        with db.connect() as c:
            return create_submission(c, fields(title="Item %d" % i), "Maya Chen", NOW)

    with ThreadPoolExecutor(max_workers=20) as pool:
        ids = list(pool.map(attempt, range(20)))
    assert len(set(ids)) == 20


def test_the_schema_still_refuses_a_blank_title_without_the_validator(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO submission (title, product, channel, status, launch_date, submitted_by,"
                     " created_at, current_version) VALUES ('  \t', 'loan', 'email', 'new', '2026-11-20',"
                     " 'Maya Chen', '2026-10-07T00:00:00Z', 1)")


def test_sql_injection_text_is_stored_verbatim(conn):
    title = "'); DROP TABLE submission;--"
    copy = "'; DELETE FROM version; -- \" OR 1=1"
    sid = create_submission(conn, fields(title=title, copy=copy, notes="'); DROP TABLE flag;--"), "Maya Chen", NOW)
    assert conn.execute("SELECT title FROM submission WHERE id = ?", (sid,)).fetchone()[0] == title
    assert conn.execute("SELECT copy FROM version WHERE submission_id = ?", (sid,)).fetchone()[0] == copy
    assert counts(conn)[:2] == [1, 1]


def test_every_statement_in_the_module_uses_placeholders():
    source = open(submit.__file__).read()
    assert not re.search(r'f["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)', source, re.I)
    assert not re.search(r'["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)[^"\']*["\']\s*(%|\.format)', source, re.I)


def test_the_new_item_shows_up_in_the_queue_with_its_flags(client):
    with db.connect() as c:
        sid = create_submission(c, fields(launch_date="2026-10-08"), "Maya Chen", NOW)
    with db.connect() as c:
        rows = list_queue(c)
        row = next(r for r in rows if r["id"] == sid)
        view = row_view(row, datetime(2026, 10, 7).date())
    assert row["status"] == "new" and row["version_number"] == 1 and row["flag_count"] >= 2
    assert view["urgency_label"] == "Rush: launches tomorrow"
    page = client.get("/").text
    assert 'href="/review/%d' % sid in page and "Spring promo" in page


def test_reset_removes_created_submissions(client):
    with db.connect() as c:
        create_submission(c, fields(), "Maya Chen", NOW)
    with db.connect() as c:
        assert counts(c)[0] == 15
        seed.reset_to_seed(c)
    with db.connect() as c:
        assert counts(c)[0] == 14
        assert c.execute("SELECT count(*) FROM submission WHERE title = 'Spring promo'").fetchone()[0] == 0
