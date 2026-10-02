import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import seed
from app.seed import SeedError, reset_to_seed, seed_all, seed_if_empty
from tests.test_rules_seed import EXPECTED, EXPECTED_PHRASE_COUNTS

ROOT = Path(seed.__file__).resolve().parent.parent
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
# One row per occurrence for phrase rules, one row for each missing-text rule that fired.
EXPECTED_ROWS = 25


def seeded_rows(conn):
    return conn.execute(
        "SELECT s.id, v.version_number, f.rule_id, f.severity, f.kind, f.matched_text, f.start_index, f.end_index"
        " FROM flag f JOIN version v ON v.id = f.version_id JOIN submission s ON s.id = v.submission_id"
        " ORDER BY s.id, v.version_number, f.id").fetchall()


def test_flag_rows_match_the_expected_table_exactly(conn):
    seed_all(conn, NOW)
    got = {}
    for sid, ver, rule, *_ in seeded_rows(conn):
        got.setdefault((sid, ver), set()).add(rule)
    assert got == {k: v for k, v in EXPECTED.items() if v}


def test_total_rows_and_phrase_occurrences(conn):
    seed_all(conn, NOW)
    rows = seeded_rows(conn)
    assert len(rows) == EXPECTED_ROWS
    phrase = Counter((r[0], r[1], r[2]) for r in rows if r[4] == "phrase")
    for (sid, ver), counts in EXPECTED_PHRASE_COUNTS.items():
        for rule, n in counts.items():
            assert phrase[(sid, ver, rule)] == n
    assert sum(1 for r in rows if r[4] == "missing") == 13


def test_phrase_rows_slice_back_to_the_stored_copy_and_missing_rows_are_null(conn):
    seed_all(conn, NOW)
    copies = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT submission_id, version_number, copy FROM version")}
    for sid, ver, rule, sev, kind, text, start, end in seeded_rows(conn):
        if kind == "phrase":
            assert copies[(sid, ver)][start:end] == text
        else:
            assert (text, start, end) == (None, None, None)


def test_integrity_and_foreign_keys_are_clean(conn):
    seed_all(conn, NOW)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT count(*) FROM flag f LEFT JOIN version v ON v.id = f.version_id"
                        " WHERE v.id IS NULL").fetchone()[0] == 0


def test_the_seeded_dismissal_has_a_matching_flag(conn):
    seed_all(conn, NOW)
    row = conn.execute(
        "SELECT count(*) FROM flag_dismissal d JOIN flag f ON f.version_id = d.version_id AND f.rule_id = d.rule_id"
    ).fetchone()
    assert row[0] >= 1
    sid, rule = conn.execute(
        "SELECT v.submission_id, d.rule_id FROM flag_dismissal d JOIN version v ON v.id = d.version_id").fetchone()
    assert (sid, rule) == (13, "R1")


def seed_file_with(tmp_path, monkeypatch, mutate):
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    mutate(data)
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(seed, "SEED_PATH", path)


def test_a_dismissal_for_a_rule_that_does_not_fire_fails_the_whole_seed(conn, tmp_path, monkeypatch):
    def mutate(data):
        s13 = next(s for s in data["submissions"] if s["seedId"] == 13)
        s13["dismissals"][0]["ruleId"] = "R3"  # R3 never applies to a loan email
    seed_file_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(SeedError, match="submission 13: dismissal of R3 on version 1 has no matching flag"):
        seed_all(conn, NOW)
    for table in ("submission", "version", "flag", "flag_dismissal", "decision", "comment"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_a_dismissal_on_the_wrong_version_fails(conn, tmp_path, monkeypatch):
    def mutate(data):
        s5 = next(s for s in data["submissions"] if s["seedId"] == 5)
        # v1 had R2 and R5; v2 is clean, so dismissing R2 on v2 points at nothing.
        s5["dismissals"] = [{"versionNumber": 2, "ruleId": "R2", "note": "n", "dismissedBy": "Alex Rivera",
                             "hoursAgo": 1}]
    seed_file_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(SeedError, match="submission 5: dismissal of R2 on version 2"):
        seed_all(conn, NOW)
    assert conn.execute("SELECT count(*) FROM submission").fetchone()[0] == 0


def test_a_dismissal_on_the_right_version_of_a_multi_version_item_is_allowed(conn, tmp_path, monkeypatch):
    def mutate(data):
        s5 = next(s for s in data["submissions"] if s["seedId"] == 5)
        s5["dismissals"] = [{"versionNumber": 1, "ruleId": "R2", "note": "n", "dismissedBy": "Alex Rivera",
                             "hoursAgo": 100}]
    seed_file_with(tmp_path, monkeypatch, mutate)
    seed_all(conn, NOW)
    assert conn.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 2


def test_engine_failure_on_the_ninth_submission_leaves_the_database_empty(conn, monkeypatch):
    real = seed.evaluate_version
    calls = []

    def flaky(c, version_id):
        calls.append(version_id)
        if len(calls) == 9:
            raise RuntimeError("engine failed")
        return real(c, version_id)
    monkeypatch.setattr(seed, "evaluate_version", flaky)
    with pytest.raises(RuntimeError):
        seed_all(conn, NOW)
    for table in ("submission", "version", "flag", "flag_dismissal", "decision", "comment"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_flags_are_computed_not_read_from_the_seed_file(conn, tmp_path, monkeypatch):
    def mutate(data):
        data["submissions"][0]["flags"] = [{"ruleId": "R7"}]  # an unknown key must not become a flag
        for s in data["submissions"]:
            for v in s["versions"]:
                v["flags"] = ["R3"]
    seed_file_with(tmp_path, monkeypatch, mutate)
    try:
        seed_all(conn, NOW)
    except SeedError:
        return  # rejected outright is also acceptable
    assert len(seeded_rows(conn)) == EXPECTED_ROWS
    assert not any(r[2] == "R7" and r[0] == 1 for r in seeded_rows(conn))


def test_editing_a_rule_changes_the_seeded_flags(conn, tmp_path, monkeypatch):
    from app import rules
    data = json.loads((ROOT / "data" / "rules.json").read_text())
    for r in data:
        if r["id"] == "R6":
            r["detection"]["phrases"] = ["no such phrase"]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(rules, "_cache", rules.load_rules(path))
    rules._phrase_patterns.cache_clear()
    try:
        seed_all(conn, NOW)
        assert conn.execute("SELECT count(*) FROM flag WHERE rule_id = 'R6'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM flag WHERE rule_id = 'R1'").fetchone()[0] == 6
    finally:
        rules._phrase_patterns.cache_clear()


def test_seed_if_empty_and_reset_also_compute_flags(conn):
    assert seed_if_empty(conn, NOW) is True
    assert conn.execute("SELECT count(*) FROM flag").fetchone()[0] == EXPECTED_ROWS
    conn.execute("DELETE FROM flag")
    conn.commit()
    reset_to_seed(conn, NOW)
    assert conn.execute("SELECT count(*) FROM flag").fetchone()[0] == EXPECTED_ROWS
    assert seed_if_empty(conn, NOW) is False


def test_no_circular_import_between_seed_rules_and_flags():
    import importlib
    import subprocess
    import sys
    for mod in ("app.seed", "app.rules", "app.flags", "app.choices"):
        out = subprocess.run([sys.executable, "-c", f"import {mod}"], cwd=ROOT, capture_output=True, text=True)
        assert out.returncode == 0, (mod, out.stderr)
    assert importlib.import_module("app.seed").PRODUCTS == ("loan", "card", "mortgage")


def test_the_seed_file_has_no_flags_key_in_the_real_data():
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    assert not any("flags" in s or any("flags" in v for v in s["versions"]) for s in data["submissions"])
