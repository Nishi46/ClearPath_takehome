import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import queue as queue_module
from app import seed
from app.queue import FILTER_OPTIONS, clean_filter, list_queue
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
RAW = json.loads((Path(seed.__file__).parent.parent / "data" / "seed.json").read_text())["submissions"]
EXPECTED_ORDER = [11, 1, 2, 14, 3, 4, 5, 13, 6, 7, 8, 9, 10, 12]


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def ids(rows):
    return [r["id"] for r in rows]


def oracle(**filters):
    """Expected ids computed straight from seed.json, independent of the SQL."""
    rows = [s for s in RAW if all(s[k] == v for k, v in filters.items())]
    return [s["seedId"] for s in sorted(rows, key=lambda s: (s["launchOffsetDays"], -s["createdHoursAgo"], s["seedId"]))]


def insert(conn, sid, launch, created, title="t"):
    conn.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by,"
                 " created_at, current_version) VALUES (?, ?, 'loan', 'email', 'new', ?, 'me', ?, 1)",
                 (sid, title, launch, created))
    conn.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (?, 1, 'c', ?)",
                 (sid, created))
    conn.commit()


# ---- ordering ----

def test_no_filter_returns_14_in_launch_order(seeded):
    assert ids(list_queue(seeded)) == EXPECTED_ORDER


def test_oracle_agrees_with_the_hand_written_order():
    assert oracle() == EXPECTED_ORDER


def test_ties_break_on_created_at_then_id(seeded):
    insert(seeded, 101, "2026-10-02", "2026-09-30T00:00:00Z")   # same launch date as #1
    insert(seeded, 102, "2026-10-02", "2026-09-30T00:00:00Z")   # same date and created_at as 101
    insert(seeded, 103, "2026-10-02", "2020-01-01T00:00:00Z")   # oldest, so first of the tie
    got = ids(list_queue(seeded))
    same_day = [i for i in got if i in (1, 101, 102, 103)]
    # 2020 first; then the two 09-30T00:00 rows in id order; #1 was created later that day (20h before NOW).
    assert same_day == [103, 101, 102, 1]
    assert got.index(103) > got.index(11)  # still after the overdue item


def test_order_is_stable_between_calls(seeded):
    assert ids(list_queue(seeded)) == ids(list_queue(seeded))


# ---- row shape ----

def test_row_fields(seeded):
    row = next(r for r in list_queue(seeded) if r["id"] == 5)
    assert set(row) == {"id", "title", "product", "channel", "launch_date", "status", "submitted_by",
                        "created_at", "version_number", "flag_count", "top_severity"}
    assert (row["title"], row["product"], row["channel"], row["status"], row["submitted_by"]) == (
        "Balance transfer email", "card", "email", "in_review", "Maya Chen")
    assert row["version_number"] == 2
    assert row["flag_count"] == 0


def test_multi_version_submissions_appear_once(seeded):
    got = ids(list_queue(seeded))
    assert len(got) == len(set(got)) == 14
    assert next(r for r in list_queue(seeded) if r["id"] == 7)["version_number"] == 2


def test_flag_count_uses_only_the_current_version(seeded):
    v1, v2 = [r[0] for r in seeded.execute("SELECT id FROM version WHERE submission_id = 5 ORDER BY version_number")]
    seeded.execute("DELETE FROM flag")  # start from known rows; the seed now computes real flags
    for vid, rule in ((v1, "R2"), (v1, "R5"), (v2, "R7")):
        seeded.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, ?, 'high', 'missing')",
                       (vid, rule))
    seeded.commit()
    row = next(r for r in list_queue(seeded) if r["id"] == 5)
    assert row["flag_count"] == 1  # v2's flag only; v1's two are history
    assert next(r for r in list_queue(seeded) if r["id"] == 1)["flag_count"] == 0


# ---- filters ----

@pytest.mark.parametrize("key,value", [
    ("status", "new"), ("status", "in_review"), ("status", "changes_requested"), ("status", "approved"),
    ("status", "rejected"), ("product", "loan"), ("product", "card"), ("product", "mortgage"),
    ("channel", "email"), ("channel", "paid_social"), ("channel", "affiliate_page"), ("channel", "display"),
])
def test_each_filter_alone_matches_the_seed(seeded, key, value):
    got = ids(list_queue(seeded, **{key: value}))
    assert got == oracle(**{key: value})
    assert got  # every option has at least one seed item


def test_known_counts(seeded):
    assert len(list_queue(seeded, status="new")) == 6
    assert len(list_queue(seeded, status="in_review")) == 3
    assert len(list_queue(seeded, status="rejected")) == 1


@pytest.mark.parametrize("filters", [
    {"status": "new", "product": "loan"},
    {"product": "mortgage", "channel": "email"},
    {"status": "approved", "product": "mortgage", "channel": "email"},
    {"status": "new", "product": "card", "channel": "display"},
])
def test_combined_filters_narrow(seeded, filters):
    assert ids(list_queue(seeded, **filters)) == oracle(**{k: v for k, v in filters.items()})


def test_impossible_combination_is_empty_not_an_error(seeded):
    assert list_queue(seeded, status="rejected", product="card") == []


def test_every_option_is_a_real_database_value(conn):
    # FILTER_OPTIONS must match the schema CHECK lists, or a filter could never match / never be offered.
    assert [v for v, _ in FILTER_OPTIONS["status"]] == list(seed.STATUSES)
    assert [v for v, _ in FILTER_OPTIONS["product"]] == list(seed.PRODUCTS)
    assert [v for v, _ in FILTER_OPTIONS["channel"]] == list(seed.CHANNELS)
    assert all(label.strip() for opts in FILTER_OPTIONS.values() for _, label in opts)


# ---- hostile and odd filter values ----

HOSTILE = ["bogus", "' OR 1=1 --", "new;DROP TABLE submission", "new' OR '1'='1", "NEW", "New", " new", "new ",
           "", "x" * 10000, "é\u202E\U0001F600", "%", "_", "new\x00", "1", "None"]


@pytest.mark.parametrize("bad", HOSTILE)
@pytest.mark.parametrize("name", ["status", "product", "channel"])
def test_unknown_values_mean_all_and_leave_the_database_intact(seeded, name, bad):
    assert ids(list_queue(seeded, **{name: bad})) == EXPECTED_ORDER
    assert seeded.execute("SELECT count(*) FROM submission").fetchone()[0] == 14
    assert seeded.execute("SELECT count(*) FROM sqlite_master WHERE name = 'submission'").fetchone()[0] == 1


@pytest.mark.parametrize("bad", [None, 5, 1.5, True, ["new"], ("new",), {"a": 1}, b"new"])
def test_non_string_values_mean_all(seeded, bad):
    assert ids(list_queue(seeded, status=bad)) == EXPECTED_ORDER


def test_one_valid_and_one_invalid_filter(seeded):
    assert ids(list_queue(seeded, status="new", product="' OR 1=1 --")) == oracle(status="new")


def test_clean_filter_unit():
    assert clean_filter("status", "new") == "new"
    assert clean_filter("status", "loan") is None  # a product is not a status
    assert clean_filter("product", "loan") == "loan"
    assert clean_filter("channel", None) is None
    with pytest.raises(KeyError):
        clean_filter("title", "x")  # only the three known filters exist


# ---- efficiency and SQL hygiene ----

def test_runs_exactly_one_statement(seeded):
    seen = []
    seeded.set_trace_callback(seen.append)
    try:
        list_queue(seeded, status="new", product="loan")
        list_queue(seeded)
    finally:
        seeded.set_trace_callback(None)
    assert len([s for s in seen if s.lstrip().upper().startswith("SELECT")]) == 2  # one per call


def test_statement_count_does_not_grow_with_more_rows(seeded):
    for i in range(50):
        insert(seeded, 200 + i, "2027-01-01", f"2026-01-{(i % 28) + 1:02d}T00:00:00Z")
    seen = []
    seeded.set_trace_callback(seen.append)
    try:
        rows = list_queue(seeded)
    finally:
        seeded.set_trace_callback(None)
    assert len(rows) == 64
    assert len(seen) == 1


def test_values_are_bound_not_built_into_sql(seeded):
    seen = []
    seeded.set_trace_callback(seen.append)
    try:
        list_queue(seeded, status="new")
    finally:
        seeded.set_trace_callback(None)
    assert "s.status = 'new'" in seen[0] or "'new'" in seen[0]  # sqlite shows the bound value, not our text
    # The queue SQL is one constant with only "?" placeholders; no SQL text is built at run time.
    sql = queue_module.sql_queue
    assert sql.count("?") == 12
    assert "%" not in sql and "{" not in sql
    source = Path(queue_module.__file__).read_text()
    calls = re.findall(r"\.execute\w*\(([^)]*)\)", source)
    # Either the shared constant or a plain string literal (never an f-string, concatenation or a variable).
    assert calls and all(c.strip().startswith(("sql_queue", '"', "'")) for c in calls)
    assert not any(re.match(r"""\s*[fF]["']""", c) for c in calls)
    assert not any("+" in c.split(",")[0] for c in calls)


def test_query_does_not_write(seeded):
    before = seeded.total_changes
    list_queue(seeded, status="new")
    assert seeded.total_changes == before


def test_empty_database_returns_an_empty_list(conn):
    assert list_queue(conn) == []
