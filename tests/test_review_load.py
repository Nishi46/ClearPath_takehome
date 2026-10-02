from datetime import datetime, timezone

import pytest

from app.review import load_review
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def rules(data):
    return [f["rule_id"] for f in data["flags"]]


def test_item_3_has_flags_and_no_decision(seeded):
    d = load_review(seeded, 3)
    assert d["submission"]["title"] == "Mortgage prequal landing page"
    assert d["version"]["version_number"] == 1
    assert sorted(rules(d)) == ["R3", "R4"]
    assert d["decision"] is None and d["dismissals"] == []
    assert [c["author"] for c in d["comments"]] == ["Alex Rivera"]  # one seeded "starting review" comment


def test_flags_ordered_phrase_by_start_then_missing(seeded):
    for sid in range(1, 15):
        d = load_review(seeded, sid)
        starts = [f["start_index"] for f in d["flags"] if f["kind"] == "phrase"]
        assert starts == sorted(starts)
        kinds = [f["kind"] for f in d["flags"]]
        assert kinds == sorted(kinds, key=lambda k: k == "missing")


def test_item_5_versions(seeded):
    cur = load_review(seeded, 5)
    assert cur["version"]["version_number"] == 2
    assert cur["flags"] == [] and cur["decision"] is None
    assert cur["version_numbers"] == [1, 2]
    old = load_review(seeded, 5, 1)
    assert sorted(rules(old)) == ["R2", "R5"]
    assert old["decision"]["outcome"] == "changes_requested"


def test_item_7_each_version_has_its_own_decision(seeded):
    assert load_review(seeded, 7, 1)["decision"]["outcome"] == "rejected"
    assert load_review(seeded, 7, 2)["decision"]["outcome"] == "approved"
    assert load_review(seeded, 7, 1)["flags"] != load_review(seeded, 7, 2)["flags"]
    kinds = [(h["kind"], h["version_number"]) for h in load_review(seeded, 7)["history"]]
    assert kinds == [("version", 1), ("decision", 1), ("version", 2), ("decision", 2)]


def test_item_13_flag_and_dismissal_both_returned(seeded):
    d = load_review(seeded, 13)
    assert rules(d) and set(rules(d)) == {"R1"}
    assert len(d["dismissals"]) == 1 and d["dismissals"][0]["rule_id"] == "R1"
    assert d["dismissals"][0]["note"] and d["dismissals"][0]["dismissed_by"] == "Alex Rivera"


def test_item_6_decision_and_later_comment(seeded):
    d = load_review(seeded, 6)
    assert d["decision"]["outcome"] == "approved"
    assert d["comments"] and d["comments"][0]["created_at"] > d["decision"]["created_at"]


@pytest.mark.parametrize("sid, v", [(9999, None), (3, 2), (3, 0), (3, -1), (0, None), (3, 10 ** 12)])
def test_missing_returns_none(seeded, sid, v):
    assert load_review(seeded, sid, v) is None


@pytest.mark.parametrize("sid, v", [("3", None), (None, None), (3.0, None), (True, None), (3, "1"), (3, 1.0), (3, True)])
def test_odd_types_return_none(seeded, sid, v):
    assert load_review(seeded, sid, v) is None


def test_version_of_another_submission_is_not_returned(seeded):
    assert load_review(seeded, 3, 2) is None  # only #5 and #7 have a v2


def test_load_does_not_write(seeded):
    before = seeded.total_changes
    for sid in range(1, 15):
        load_review(seeded, sid)
    assert seeded.total_changes == before


def test_comments_are_only_for_the_selected_version(seeded):
    for sid in range(1, 15):
        d = load_review(seeded, sid)
        n = seeded.execute("SELECT count(*) FROM comment WHERE submission_id=? AND version_number=?",
                           (sid, d["version"]["version_number"])).fetchone()[0]
        assert len(d["comments"]) == n
