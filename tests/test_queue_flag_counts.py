import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import queue as queue_module
from app.queue import list_queue
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
ROOT = Path(queue_module.__file__).resolve().parent.parent

# Distinct rules on each submission's current version, dismissed rules left out (decisions 1 and 2).
EXPECTED = {1: 3, 2: 2, 3: 2, 4: 2, 5: 0, 6: 0, 7: 0, 8: 3, 9: 1, 10: 0, 11: 0, 12: 1, 13: 0, 14: 3}


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def counts(conn, **filters):
    return {r["id"]: r["flag_count"] for r in list_queue(conn, **filters)}


def version_id(conn, sid, number):
    return conn.execute("SELECT id FROM version WHERE submission_id = ? AND version_number = ?",
                        (sid, number)).fetchone()[0]


def test_counts_on_the_unfiltered_seed(seeded):
    assert counts(seeded) == EXPECTED


def test_item_8_shows_distinct_rules_not_occurrences(seeded):
    rows = seeded.execute("SELECT count(*) FROM flag WHERE version_id = ?", (version_id(seeded, 8, 1),)).fetchone()[0]
    assert rows == 6 and counts(seeded)[8] == 3  # six rows (R1 x2, R4, R6 x3) are three rules


def test_item_5_counts_its_current_version_only(seeded):
    assert seeded.execute("SELECT count(*) FROM flag WHERE version_id = ?", (version_id(seeded, 5, 1),)).fetchone()[0] == 2
    assert counts(seeded)[5] == 0


def test_item_13_dismissed_rule_is_not_counted_but_its_flag_row_remains(seeded):
    assert seeded.execute("SELECT count(*) FROM flag WHERE version_id = ?", (version_id(seeded, 13, 1),)).fetchone()[0] == 2
    assert counts(seeded)[13] == 0


def test_a_dismissal_lowers_the_count_by_one_rule(seeded):
    before = counts(seeded)[1]
    seeded.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                   " VALUES (?, 'R2', 'n', 'Alex Rivera', '2026-10-01T12:00:00Z')", (version_id(seeded, 1, 1),))
    assert counts(seeded)[1] == before - 1 == 2


def test_dismissals_on_another_version_or_rule_do_not_change_the_count(seeded):
    ins = ("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
           " VALUES (?, ?, 'n', 'Alex Rivera', '2026-10-01T12:00:00Z')")
    seeded.execute(ins, (version_id(seeded, 5, 1), "R2"))        # an old version of #5
    seeded.execute(ins, (version_id(seeded, 1, 1), "R6"))        # a rule that did not fire on #1
    seeded.execute(ins, (version_id(seeded, 2, 1), "R1"))        # a rule that did not fire on #2
    assert counts(seeded) == EXPECTED


def test_every_rule_dismissed_gives_zero(seeded):
    vid = version_id(seeded, 1, 1)
    for rule in ("R1", "R2", "R5"):
        seeded.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                       " VALUES (?, ?, 'n', 'Alex Rivera', '2026-10-01T12:00:00Z')", (vid, rule))
    assert counts(seeded)[1] == 0


def test_only_the_current_version_counts_when_a_new_version_has_flags(seeded):
    sid = 6  # clean, approved, one version
    seeded.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (6, 2, 'x', 'z')")
    seeded.execute("UPDATE submission SET current_version = 2 WHERE id = 6")
    v2 = version_id(seeded, 6, 2)
    seeded.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R3', 'high', 'missing')", (v2,))
    assert counts(seeded)[sid] == 1
    seeded.execute("UPDATE submission SET current_version = 1 WHERE id = 6")
    assert counts(seeded)[sid] == 0


def test_duplicate_rows_of_one_rule_count_once(seeded):
    vid = version_id(seeded, 6, 1)
    for _ in range(3):
        seeded.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R3', 'high', 'missing')", (vid,))
    assert counts(seeded)[6] == 1


def test_filters_and_order_are_unchanged(seeded):
    assert [r["id"] for r in list_queue(seeded)] == [11, 1, 2, 14, 3, 4, 5, 13, 6, 7, 8, 9, 10, 12]
    assert counts(seeded, status="approved") == {13: 0, 6: 0, 7: 0}
    assert counts(seeded, product="mortgage") == {14: 3, 3: 2, 6: 0, 9: 1, 12: 1}
    assert counts(seeded, status="rejected", product="card") == {}


def test_still_one_query_for_the_whole_list(seeded):
    seen = []
    seeded.set_trace_callback(seen.append)
    try:
        list_queue(seeded)
    finally:
        seeded.set_trace_callback(None)
    assert len([s for s in seen if s.lstrip().upper().startswith("SELECT")]) == 1


def test_empty_database_and_no_flags_give_zero(conn):
    assert list_queue(conn) == []
    seed_all(conn, NOW)
    conn.execute("DELETE FROM flag")
    assert set(counts(conn).values()) == {0}


def test_page_shows_the_numbers_and_no_dashes(client):
    html = client.get("/").text
    cells = re.findall(r'<td data-label="Flags"><span>([^<]*)</span></td>', html)
    assert len(cells) == 14 and all(c.isdigit() for c in cells)
    assert sorted(int(c) for c in cells) == sorted(EXPECTED.values())


def test_page_still_shows_three_for_item_1_and_zero_for_clean_items(client):
    html = client.get("/").text
    def flag_cell(sid):
        row = re.search(rf'<tr[^>]*>(?:(?!</tr>).)*/review/{sid}"(?:(?!</tr>).)*</tr>', html, re.S).group(0)
        return re.search(r'data-label="Flags"><span>(\d+)</span>', row).group(1)
    assert (flag_cell(1), flag_cell(8), flag_cell(6), flag_cell(10), flag_cell(13)) == ("3", "3", "0", "0", "0")


def test_no_pending_wording_or_switch_remains():
    assert not hasattr(queue_module, "FLAGS_READY") and not hasattr(queue_module, "FLAGS_PENDING_TITLE")
    src = (ROOT / "app" / "queue.py").read_text()
    assert "phase 3" not in src.lower()
    assert not re.search(r'f"[^"]*(SELECT|INSERT|UPDATE|DELETE)', src)
