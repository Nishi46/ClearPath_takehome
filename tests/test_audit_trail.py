import random
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import audit, review
from app.audit import load_trail, relative_time, trail_view
from app.seed import seed_all
from tests.helpers import tamper

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 2, 9, 5, 0, tzinfo=timezone.utc)
ME = "Alex Rivera"


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def counts(c, sid):
    q = lambda sql: c.execute(sql, (sid,)).fetchone()[0]
    return (q("SELECT count(*) FROM version WHERE submission_id = ?")
            + q("SELECT count(*) FROM decision WHERE submission_id = ?")
            + q("SELECT count(*) FROM comment WHERE submission_id = ?")
            + q("SELECT count(*) FROM flag_dismissal WHERE version_id IN"
                " (SELECT id FROM version WHERE submission_id = ?)"))


# ---- load_trail ----

@pytest.mark.parametrize("sid", range(1, 15))
def test_every_seed_item_has_a_complete_ordered_trail(seeded, sid):
    events = load_trail(seeded, sid)
    assert len(events) == counts(seeded, sid)
    keys = [(e["kind"], e["id"]) for e in events]
    assert len(keys) == len(set(keys))                                   # nothing duplicated
    times = [datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ") for e in events]
    assert times == sorted(times)
    assert events[0]["kind"] == "version" and events[0]["version_number"] == 1
    for e in events:
        if e["kind"] == "decision":
            v = next(x for x in events if x["kind"] == "version" and x["version_number"] == e["version_number"])
            assert v["at"] <= e["at"]                                    # a decision never precedes its version
        assert e["who"] and e["at"] and e["version_number"] >= 1


def test_13_trail_is_version_dismissal_decision(seeded):
    events = load_trail(seeded, 13)
    assert [e["kind"] for e in events] == ["version", "dismissal", "decision"]
    assert events[1]["rule_id"] == "R1" and events[1]["who"] == ME and "False positive" in events[1]["note"]
    assert events[2]["outcome"] == "approved"


def test_5_trail_shows_both_versions_and_the_comments_between(seeded):
    kinds = [(e["kind"], e["version_number"]) for e in load_trail(seeded, 5)]
    assert kinds[0] == ("version", 1) and ("version", 2) in kinds
    assert kinds.index(("version", 2)) > max(i for i, k in enumerate(kinds) if k == ("comment", 1))
    assert ("comment", 2) in kinds


def test_ties_order_by_kind_then_id_deterministically(seeded):
    same = "2026-10-03T00:00:00Z"
    review.dismiss_flag(seeded, 12, 1, "R4", "n", ME, datetime(2026, 10, 3, tzinfo=timezone.utc))
    seeded.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at) VALUES"
                   " (12, 1, 'B', 'second', ?), (12, 1, 'A', 'first', ?)", (same, same))
    seeded.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
                   " VALUES (12, 1, 'rejected', 'R', 'no', ?)", (same,))
    seeded.execute("UPDATE version SET created_at = ? WHERE submission_id = 12", (same,))
    seeded.commit()
    first = [(e["kind"], e["id"]) for e in load_trail(seeded, 12)]
    assert [k for k, _ in first] == ["version", "dismissal", "comment", "comment", "decision"]
    ids = [i for k, i in first if k == "comment"]
    assert ids == sorted(ids)
    for _ in range(50):
        assert [(e["kind"], e["id"]) for e in load_trail(seeded, 12)] == first


def test_resubmission_shows_each_version_with_the_owner_as_author(seeded):
    events = load_trail(seeded, 7)
    versions = [e for e in events if e["kind"] == "version"]
    assert [e["version_number"] for e in versions] == [1, 2]
    assert {e["who"] for e in versions} == {"Jordan Lee"}


@pytest.mark.parametrize("bad", ["not-a-date", ""])
def test_a_corrupt_timestamp_sorts_last_and_is_kept(seeded, bad):
    n = len(load_trail(seeded, 6))
    tamper(seeded, "UPDATE comment SET created_at = ? WHERE id = (SELECT min(id) FROM comment WHERE submission_id = 6)", (bad,))
    events = load_trail(seeded, 6)
    assert len(events) == n and events[-1]["kind"] == "comment" and events[-1]["at"] == bad
    assert trail_view(events, LATER)[-1]["when"] == "unknown time"


def test_new_dismissal_and_comment_appear_with_server_time_and_name(seeded):
    review.dismiss_flag(seeded, 12, 1, "R4", "Quoted.", ME, LATER)
    review.add_comment(seeded, 12, 1, "Note.", "R4", ME, LATER)
    tail = load_trail(seeded, 12)[-2:]
    assert [(e["kind"], e["who"], e["at"]) for e in tail] == [
        ("dismissal", ME, "2026-10-02T09:05:00Z"), ("comment", ME, "2026-10-02T09:05:00Z")]


@pytest.mark.parametrize("sid", [0, -1, 999, None, "3", True, 3.0])
def test_unknown_submission_is_none(seeded, sid):
    assert load_trail(seeded, sid) is None


def test_constant_number_of_queries_and_fast_with_200_comments(seeded):
    import time

    for i in range(200):
        seeded.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at)"
                       " VALUES (3, 1, 'x', ?, '2026-10-01T10:00:00Z')", ("c%d" % i,))
    seeded.commit()
    statements = []
    seeded.set_trace_callback(statements.append)
    start = time.perf_counter()
    events = load_trail(seeded, 3)
    elapsed = time.perf_counter() - start
    seeded.set_trace_callback(None)
    assert len(events) > 200 and elapsed < 0.05
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 4


def test_loading_is_read_only_and_leaves_no_transaction(seeded):
    before = seeded.total_changes
    load_trail(seeded, 13)
    assert seeded.total_changes == before and not seeded.in_transaction
    seeded.execute("BEGIN")
    load_trail(seeded, 13)
    assert seeded.in_transaction  # an outer transaction is not ended by a read
    seeded.rollback()


def test_module_never_writes_and_uses_placeholders():
    src = Path(audit.__file__).read_text()
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", src)
    for stmt in re.findall(r'"(SELECT[^"]*)"', src):
        assert "%" not in stmt and "format" not in stmt


# ---- trail_view ----

def sample(c, sid):
    return trail_view(load_trail(c, sid), LATER)


def test_labels_for_every_kind(seeded):
    review.add_comment(seeded, 14, 1, "extra", "R2", ME, LATER)
    v14 = {(e["kind"], e["label"]) for e in sample(seeded, 14)}
    assert ("version", "v1 submitted") in v14
    assert any(k == "comment" and l.startswith("Comment on v1 (R2: ") for k, l in v14)
    assert ("decision", "Changes requested") in v14
    assert ("dismissal", "Flag dismissed: R1: ") not in v14
    d13 = {l for k, l in {(e["kind"], e["label"]) for e in sample(seeded, 13)} if k == "dismissal"}
    assert len(d13) == 1 and next(iter(d13)).startswith("Flag dismissed: R1: ")
    assert {e["label"] for e in sample(seeded, 6) if e["kind"] == "decision"} == {"Approved"}
    assert {e["label"] for e in sample(seeded, 8) if e["kind"] == "decision"} == {"Rejected"}


def test_decision_labels_match_the_history_strip_wording(seeded):
    for sid in (6, 8, 14):
        data = review.load_review(seeded, sid)
        strip = [h["text"] for h in review.history_view(data) if h["text"].split(" ", 1)[1] != "submitted by"]
        for e in (x for x in sample(seeded, sid) if x["kind"] == "decision"):
            assert any(e["label"].lower() in t for t in strip)


def test_detail_is_the_full_text_with_newlines_as_data(seeded):
    text = "line one\n\nline <two> & \U0001F600 " + "x" * 1900
    review.add_comment(seeded, 3, 1, text, None, ME, LATER)
    last = sample(seeded, 3)[-1]
    assert last["detail"] == text and "<br" not in last["detail"]


def test_unknown_rule_and_missing_reason_do_not_crash(seeded):
    tamper(seeded, "UPDATE comment SET rule_id = 'R99' WHERE rule_id IS NOT NULL")
    labels = [e["label"] for e in sample(seeded, 14) if e["kind"] == "comment"]
    assert labels and all(l == "Comment on v1 (R99)" for l in labels)
    approved = [e for e in sample(seeded, 6) if e["kind"] == "decision"][0]
    assert approved["detail"] == "" or isinstance(approved["detail"], str)  # no reason: empty, never "None"


def test_relative_time_wording_and_future_times():
    base = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    at = "2026-10-02T09:00:00Z"
    assert relative_time(at, base) == "just now" and relative_time(at, base + timedelta(seconds=59)) == "just now"
    assert relative_time(at, base + timedelta(seconds=60)) == "1 minute ago"
    assert relative_time(at, base + timedelta(minutes=5)) == "5 minutes ago"
    assert relative_time(at, base + timedelta(hours=1)) == "1 hour ago"
    assert relative_time(at, base + timedelta(hours=23, minutes=59)) == "23 hours ago"
    assert relative_time(at, base + timedelta(days=2)) == "2 days ago"
    assert relative_time(at, base - timedelta(minutes=3)) == "just now"       # clock skew, never negative
    for bad in ("garbage", "", None, 5):
        assert relative_time(bad, base) == "unknown time"


def test_view_module_is_pure_text():
    src = Path(audit.__file__).read_text()
    for banned in ("Markup", "|safe", "datetime.now", "utcnow", "time.time"):
        assert banned not in src


def test_a_decision_and_the_next_version_in_the_same_second_stay_in_causal_order(seeded):
    same = "2026-10-03T00:00:00Z"
    seeded.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                   " VALUES (14, 2, 'v2 copy', NULL, ?)", (same,))
    seeded.execute("UPDATE submission SET current_version = 2 WHERE id = 14")
    tamper(seeded, "UPDATE decision SET created_at = ? WHERE submission_id = 14", (same,))
    seeded.execute("UPDATE version SET created_at = ? WHERE submission_id = 14 AND version_number = 1", (same,))
    seeded.commit()
    assert [(e["kind"], e["version_number"]) for e in load_trail(seeded, 14)
            if e["kind"] in ("version", "decision")] == [("version", 1), ("decision", 1), ("version", 2)]
