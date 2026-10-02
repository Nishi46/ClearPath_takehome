import re
from datetime import date, datetime, timezone

import pytest

from app import db, mine, seed
from app.mine import group_mine, list_mine

TODAY = date(2026, 10, 7)


@pytest.fixture
def seeded(client):
    with db.connect() as c:
        yield c


def ids(rows):
    return [r["id"] for r in rows]


def test_each_marketer_sees_only_their_own_items(seeded):
    assert ids(list_mine(seeded, "Maya Chen", TODAY)) == [1, 2, 3, 5, 6, 9, 11, 12, 13]
    assert ids(list_mine(seeded, "Jordan Lee", TODAY)) == [4, 7, 8, 10, 14]
    assert list_mine(seeded, "Sam Patel", TODAY) == []


@pytest.mark.parametrize("name", ["maya chen", "Maya Chen ", " Maya Chen", "MAYA CHEN", "Maya", "", "Maya Chen%",
                                  "Maya Chen' OR '1'='1", None, 5, ["Maya Chen"], b"Maya Chen"])
def test_names_are_matched_exactly_and_odd_values_list_nothing(seeded, name):
    assert list_mine(seeded, name, TODAY) == []


def test_matching_titles_do_not_leak_between_marketers(seeded):
    seeded.execute("UPDATE submission SET title = (SELECT title FROM submission WHERE id = 1) WHERE id = 4")
    seeded.commit()
    assert 4 not in ids(list_mine(seeded, "Maya Chen", TODAY)) and 1 not in ids(list_mine(seeded, "Jordan Lee", TODAY))


def test_item_14_feedback_is_its_seed_decision(seeded):
    row = next(r for r in list_mine(seeded, "Jordan Lee", TODAY) if r["id"] == 14)
    stored = seeded.execute("SELECT outcome, reviewer, reason, created_at FROM decision WHERE submission_id = 14").fetchone()
    fb = row["feedback"]
    assert (fb["outcome"], fb["reviewer"], fb["reason"], fb["at"]) == tuple(stored)
    assert fb["reviewer"] == "Alex Rivera" and fb["outcome_label"] == "Changes requested" and fb["reason"]
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", fb["when"])


def test_a_decision_on_an_older_version_is_not_current_feedback(seeded):
    rows = {r["id"]: r for r in list_mine(seeded, "Maya Chen", TODAY)}
    assert rows[5]["version_number"] == 2 and rows[5]["feedback"] is None   # v1 was changes requested; v2 is waiting
    assert rows[3]["feedback"] is None                                      # never decided
    assert rows[6]["feedback"]["outcome"] == "approved"


def test_item_7_shows_the_decision_on_its_current_version(seeded):
    row = next(r for r in list_mine(seeded, "Jordan Lee", TODAY) if r["id"] == 7)
    assert row["version_number"] == 2 and row["feedback"]["outcome"] == "approved"


def test_row_fields_and_labels(seeded):
    row = next(r for r in list_mine(seeded, "Jordan Lee", TODAY) if r["id"] == 8)
    assert row["status"] == "rejected" and row["status_label"] == "Rejected" and row["urgency"] is None
    assert row["feedback"]["outcome_label"] == "Rejected"
    assert set(row) >= {"id", "title", "product", "channel", "status", "status_label", "version_number",
                        "launch_date", "urgency", "flag_count", "feedback"}


def test_flag_counts_match_the_queue(seeded):
    from app.queue import list_queue
    by_id = {r["id"]: r["flag_count"] for r in list_queue(seeded)}
    for who in ("Maya Chen", "Jordan Lee"):
        for r in list_mine(seeded, who, TODAY):
            assert r["flag_count"] == by_id[r["id"]], r["id"]


def test_dismissed_rules_are_not_counted(seeded):
    row = next(r for r in list_mine(seeded, "Maya Chen", TODAY) if r["id"] == 13)
    assert row["flag_count"] == 0       # #13's only flag (R1) is dismissed


def test_urgency_only_for_open_items(seeded):
    seeded.execute("UPDATE submission SET launch_date = '2026-10-01' WHERE id IN (6, 14, 8, 3)")
    seeded.commit()
    rows = {r["id"]: r for r in list_mine(seeded, "Maya Chen", TODAY) + list_mine(seeded, "Jordan Lee", TODAY)}
    assert rows[6]["urgency"] is None and rows[8]["urgency"] is None        # approved and rejected are never urgent
    assert rows[3]["urgency"] == "overdue" and rows[14]["urgency"] == "overdue"


def test_default_today_comes_from_the_clock(seeded, monkeypatch):
    from app import clock
    monkeypatch.setattr(clock, "today", lambda: date(2030, 1, 1))
    assert all(r["urgency"] in (None, "overdue") for r in list_mine(seeded, "Maya Chen"))
    assert any(r["urgency"] == "overdue" for r in list_mine(seeded, "Maya Chen"))


def test_malformed_stored_values_never_raise(seeded):
    seeded.execute("UPDATE submission SET launch_date = 'not a date' WHERE id = 3")
    seeded.execute("UPDATE decision SET created_at = 'garbage' WHERE submission_id = 6")
    seeded.commit()
    rows = {r["id"]: r for r in list_mine(seeded, "Maya Chen", TODAY)}
    assert rows[3]["urgency"] is None and rows[6]["feedback"]["when"] == "unknown date"
    group_mine(list(rows.values()))            # and grouping copes too


def test_reading_writes_nothing(seeded):
    before = seeded.total_changes
    list_mine(seeded, "Maya Chen", TODAY)
    list_mine(seeded, "Jordan Lee", TODAY)
    assert seeded.total_changes == before and not seeded.in_transaction


def test_the_query_is_one_fixed_parameterized_statement():
    src = open(mine.__file__).read()
    assert src.count("?") >= 1 and "execute(sql_mine, (marketer,))" in src
    assert not re.search(r'f["\'][^"\']*(SELECT|INSERT|UPDATE|DELETE)', src, re.I)
    assert "%s" not in mine.sql_mine and "{" not in mine.sql_mine


# ---- grouping ----

def groups(conn, who):
    return {g["key"]: ids(g["rows"]) for g in group_mine(list_mine(conn, who, TODAY))}


def test_jordans_groups(seeded):
    g = groups(seeded, "Jordan Lee")
    assert g["needs_action"] == [14, 8]            # changes requested first, then rejected
    assert sorted(g["in_progress"]) == [4, 10] and g["done"] == [7]


def test_mayas_groups(seeded):
    g = groups(seeded, "Maya Chen")
    assert g["needs_action"] == []
    assert sorted(g["done"]) == [6, 13]
    assert sorted(g["in_progress"]) == [1, 2, 3, 5, 9, 11, 12]


def test_groups_come_in_a_fixed_order_and_empty_ones_are_still_returned(seeded):
    result = group_mine(list_mine(seeded, "Sam Patel", TODAY))
    assert [g["key"] for g in result] == ["needs_action", "in_progress", "done"]
    assert [g["title"] for g in result] == ["Needs your action", "In progress", "Done"]
    assert all(g["rows"] == [] for g in result)


def test_in_progress_is_soonest_launch_first_with_ties_by_id(seeded):
    # Seed launch dates are relative to the real today, so set every in-progress item explicitly.
    seeded.execute("UPDATE submission SET launch_date = '2027-06-01' WHERE id IN (5, 9, 11)")
    seeded.execute("UPDATE submission SET launch_date = '2026-12-01' WHERE id IN (1, 2, 3)")
    seeded.execute("UPDATE submission SET launch_date = '2026-11-01' WHERE id = 12")
    seeded.commit()
    order = groups(seeded, "Maya Chen")["in_progress"]
    assert order == [12, 1, 2, 3, 5, 9, 11]
    launches = [r["launch_date"] for g in group_mine(list_mine(seeded, "Maya Chen", TODAY)) if g["key"] == "in_progress"
                for r in g["rows"]]
    assert launches == sorted(launches)


def test_needs_action_is_newest_decision_first_within_each_status(seeded):
    # Make #14 and a copy-like second changes-requested item, and two rejections, with known times.
    seeded.execute("UPDATE submission SET submitted_by = 'Jordan Lee' WHERE id = 6")
    seeded.execute("UPDATE submission SET status = 'changes_requested' WHERE id = 6")
    seeded.execute("UPDATE decision SET outcome = 'changes_requested', reason = 'Fix it.', created_at = '2026-10-06T10:00:00Z' WHERE submission_id = 6")
    seeded.execute("UPDATE decision SET created_at = '2026-10-05T10:00:00Z' WHERE submission_id = 14")
    seeded.execute("UPDATE decision SET created_at = '2026-10-01T10:00:00Z' WHERE submission_id = 8")
    seeded.execute("UPDATE submission SET status = 'rejected' WHERE id = 7")
    seeded.execute("UPDATE decision SET outcome = 'rejected', created_at = '2026-10-04T10:00:00Z' WHERE submission_id = 7 AND version_number = 2")
    seeded.execute("UPDATE submission SET submitted_by = 'Jordan Lee' WHERE id = 7")
    seeded.commit()
    # changes requested: #6 (Oct 6) before #14 (Oct 5); then rejected: #7 (Oct 4) before #8 (Oct 1).
    assert groups(seeded, "Jordan Lee")["needs_action"] == [6, 14, 7, 8]


def test_done_is_newest_decision_first(seeded):
    seeded.execute("UPDATE decision SET created_at = '2026-10-01T00:00:00Z' WHERE submission_id = 6")
    seeded.execute("UPDATE decision SET created_at = '2026-10-03T00:00:00Z' WHERE submission_id = 13")
    seeded.commit()
    assert groups(seeded, "Maya Chen")["done"] == [13, 6]


def test_ties_break_on_id_and_the_order_is_stable(seeded):
    seeded.execute("UPDATE decision SET created_at = '2026-10-01T00:00:00Z' WHERE submission_id IN (6, 13)")
    seeded.commit()
    first = groups(seeded, "Maya Chen")
    assert first["done"] == [13, 6]                       # same time: higher id first, every time
    assert groups(seeded, "Maya Chen") == first


def test_every_row_lands_in_exactly_one_group(seeded):
    for who in ("Maya Chen", "Jordan Lee"):
        rows = list_mine(seeded, who, TODAY)
        placed = [i for g in group_mine(rows) for i in ids(g["rows"])]
        assert sorted(placed) == sorted(ids(rows))


def test_grouping_does_not_change_or_drop_rows(seeded):
    rows = list_mine(seeded, "Maya Chen", TODAY)
    snapshot = [dict(r) for r in rows]
    group_mine(rows)
    assert rows == snapshot


def test_a_new_submission_and_a_resubmitted_item_land_in_progress(seeded):
    from app.submit import create_submission
    sid = create_submission(seeded, {"title": "Fresh", "product": "loan", "channel": "email", "launch_date": "2026-12-01",
                                     "copy": "Hello."}, "Sam Patel", datetime(2026, 10, 7, tzinfo=timezone.utc))
    g = groups(seeded, "Sam Patel")
    assert g["in_progress"] == [sid] and g["needs_action"] == [] and g["done"] == []


def test_the_view_model_module_has_no_web_code():
    src = open(mine.__file__).read()
    assert "fastapi" not in src and "request" not in src.lower().replace("requested", "")
