import json
import sqlite3
from pathlib import Path

import pytest

from app import flags as flagmod
from app.flags import evaluate_version, store_flags
from app.rules import Flag, evaluate
from tests.test_schema_submission_version import add_submission, add_version

ROOT = Path(flagmod.__file__).resolve().parent.parent
SEED = json.loads((ROOT / "data" / "seed.json").read_text())


def seed_item(seed_id):
    return next(s for s in SEED["submissions"] if s["seedId"] == seed_id)


@pytest.fixture
def add(conn):
    """Insert a submission with one version and return (version_id, copy)."""
    def make(product="loan", channel="email", copy="Some ad copy"):
        sid = add_submission(conn, product=product, channel=channel)
        add_version(conn, sid, copy=copy)
        return conn.execute("SELECT id FROM version WHERE submission_id = ?", (sid,)).fetchone()[0]
    return make


def rows(conn, version_id):
    return [tuple(r) for r in conn.execute(
        "SELECT rule_id, severity, kind, matched_text, start_index, end_index FROM flag"
        " WHERE version_id = ? ORDER BY id", (version_id,))]


def seed_1_version(add):
    s = seed_item(1)
    return add(s["product"], s["channel"], s["versions"][0]["copy"]), s["versions"][0]["copy"]


def test_seed_1_stores_three_rows_with_the_right_shape(conn, add):
    vid, copy = seed_1_version(add)
    flags = evaluate_version(conn, vid)
    store_flags(conn, vid, flags)
    got = rows(conn, vid)
    assert [r[0] for r in got] == ["R1", "R2", "R5"]
    assert [(r[1], r[2]) for r in got] == [("high", "phrase"), ("high", "missing"), ("medium", "missing")]
    rule, sev, kind, text, start, end = got[0]
    assert text == "Guaranteed approval" and copy[start:end] == text
    for r in got[1:]:
        assert r[3:] == (None, None, None)


def test_evaluate_version_matches_the_pure_engine_and_writes_nothing(conn, add):
    vid, copy = seed_1_version(add)
    s = seed_item(1)
    assert evaluate_version(conn, vid) == evaluate(s["product"], s["channel"], copy)
    assert rows(conn, vid) == []


def test_storing_twice_leaves_the_same_rows(conn, add):
    vid, _ = seed_1_version(add)
    flags = evaluate_version(conn, vid)
    store_flags(conn, vid, flags)
    first = rows(conn, vid)
    store_flags(conn, vid, flags)
    assert rows(conn, vid) == first and len(first) == 3


def test_empty_list_clears_stale_rows_and_a_clean_version_stores_nothing(conn, add):
    vid, _ = seed_1_version(add)
    store_flags(conn, vid, evaluate_version(conn, vid))
    store_flags(conn, vid, [])
    assert rows(conn, vid) == []
    clean = add("mortgage", "email", "Hello. Equal Housing Lender.")
    store_flags(conn, clean, evaluate_version(conn, clean))
    assert rows(conn, clean) == []


def test_only_the_named_version_is_touched(conn, add):
    a, _ = seed_1_version(add)
    b, _ = seed_1_version(add)
    store_flags(conn, a, evaluate_version(conn, a))
    store_flags(conn, b, evaluate_version(conn, b))
    store_flags(conn, a, [])
    assert rows(conn, a) == [] and len(rows(conn, b)) == 3


@pytest.mark.parametrize("copy", [
    "He said \"guaranteed approval\" and it's 'no credit check'", "<script>no credit check</script>",
    "x'; DROP TABLE flag; -- guaranteed approval", "guaranteed approval\u0000", "\U0001f600 guaranteed approval",
])
def test_hostile_matched_text_is_stored_literally_and_tables_survive(conn, add, copy):
    vid = add("loan", "email", copy)
    flags = evaluate_version(conn, vid)
    store_flags(conn, vid, flags)
    stored = [r for r in rows(conn, vid) if r[2] == "phrase"]
    assert stored and all(copy[r[4]:r[5]] == r[3] for r in stored)
    assert conn.execute("SELECT count(*) FROM flag").fetchone()[0] >= 1
    assert conn.execute("SELECT count(*) FROM submission").fetchone()[0] == 1


def test_all_seed_versions_round_trip_through_the_table(conn, add):
    for s in SEED["submissions"]:
        for v in s["versions"]:
            vid = add(s["product"], s["channel"], v["copy"])
            flags = evaluate_version(conn, vid)
            store_flags(conn, vid, flags)
            assert [(r[0], r[3], r[4], r[5]) for r in rows(conn, vid)] == \
                [(f.rule_id, f.matched_text, f.start, f.end) for f in flags]


def test_the_check_constraint_rejects_a_hand_built_bad_row_but_never_an_engine_flag(conn, add):
    vid, _ = seed_1_version(add)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (?, 'R1', 'high', 'phrase')", (vid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO flag (version_id, rule_id, severity, kind, matched_text) VALUES (?, 'R3', 'high', 'missing', 'x')", (vid,))
    store_flags(conn, vid, evaluate_version(conn, vid))  # engine output satisfies the same checks


@pytest.mark.parametrize("bad", [999_999, 0, -1])
def test_unknown_version_raises_and_does_nothing(conn, add, bad):
    vid, _ = seed_1_version(add)
    store_flags(conn, vid, evaluate_version(conn, vid))
    with pytest.raises(ValueError, match="No such version"):
        store_flags(conn, bad, [])
    with pytest.raises(ValueError, match="No such version"):
        evaluate_version(conn, bad)
    assert len(rows(conn, vid)) == 3


@pytest.mark.parametrize("bad", [None, "1", 1.0, True, [1], "1; DROP TABLE flag"])
def test_version_id_must_be_a_plain_integer(conn, bad):
    with pytest.raises(TypeError):
        store_flags(conn, bad, [])
    with pytest.raises(TypeError):
        evaluate_version(conn, bad)


def test_flags_must_be_flag_objects(conn, add):
    vid, _ = seed_1_version(add)
    store_flags(conn, vid, evaluate_version(conn, vid))
    for bad in ([("R1", "high", "phrase", "x", 0, 1)], [None], ["R1"], [{"rule_id": "R1"}]):
        with pytest.raises(TypeError):
            store_flags(conn, vid, bad)
    assert len(rows(conn, vid)) == 3


def test_a_failure_part_way_keeps_the_old_rows(conn, add):
    vid, _ = seed_1_version(add)
    store_flags(conn, vid, evaluate_version(conn, vid))
    before = rows(conn, vid)
    good = Flag("R1", "high", "phrase", "x", 0, 1)
    bad = Flag("R1", "high", "phrase", "y", 0, 1)
    object.__setattr__(bad, "severity", "critical")  # slips past the dataclass, hits the table CHECK
    with pytest.raises(sqlite3.IntegrityError):
        store_flags(conn, vid, [good, bad])
    assert rows(conn, vid) == before


def test_caller_rollback_removes_everything_stored(conn, add):
    vid, _ = seed_1_version(add)
    conn.commit()
    store_flags(conn, vid, evaluate_version(conn, vid))
    assert len(rows(conn, vid)) == 3
    conn.rollback()
    assert rows(conn, vid) == []


def test_function_does_not_commit(conn, add):
    vid, _ = seed_1_version(add)
    conn.commit()
    store_flags(conn, vid, evaluate_version(conn, vid))
    assert conn.in_transaction


def test_foreign_keys_stay_enforced_and_flags_cascade_with_the_version(conn, add):
    vid, _ = seed_1_version(add)
    store_flags(conn, vid, evaluate_version(conn, vid))
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("DELETE FROM submission")
    assert conn.execute("SELECT count(*) FROM flag").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_engine_error_propagates_and_stores_nothing(conn, add):
    vid, _ = seed_1_version(add)
    conn.execute("UPDATE submission SET product = 'loan'")
    # A product the engine rejects cannot be stored by the schema, so simulate with a patched engine.
    import app.flags as f
    original = f.evaluate
    f.evaluate = lambda *a: (_ for _ in ()).throw(RuntimeError("engine failed"))
    try:
        with pytest.raises(RuntimeError):
            evaluate_version(conn, vid)
    finally:
        f.evaluate = original
    assert rows(conn, vid) == []


def test_source_uses_only_bound_parameters():
    src = (ROOT / "app" / "flags.py").read_text()
    import re
    assert not re.search(r'f"[^"]*(SELECT|INSERT|UPDATE|DELETE)', src)
    assert not re.search(r"%\s*\(|\.format\(", src)
    for banned in ("eval(", "exec(", "pickle"):
        assert banned not in src
