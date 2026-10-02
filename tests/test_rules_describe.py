import json
import sqlite3
from pathlib import Path

import pytest

from app import rules
from app.rules import Flag, UNKNOWN_RULE_NAME, all_rules, describe, evaluate

ROOT = Path(rules.__file__).resolve().parent.parent
RAW = {r["id"]: r for r in json.loads((ROOT / "data" / "rules.json").read_text())}
WORDS = {"high": "High", "medium": "Medium", "low": "Low"}


def flag_for(rule):
    if rule.kind == "phrase":
        return Flag(rule.id, rule.severity, "phrase", "x", 0, 1)
    return Flag(rule.id, rule.severity, "missing")


@pytest.mark.parametrize("rule", all_rules(), ids=lambda r: r.id)
def test_every_rule_describes_from_the_rules_file(rule):
    d = describe(flag_for(rule))
    raw = RAW[rule.id]
    assert d["rule_id"] == rule.id and d["known"] is True
    assert d["name"] == raw["name"] and d["explanation"] == raw["description"] and d["snippet"] == raw["snippetText"]
    assert d["severity"] == raw["severity"] and d["severity_label"] == WORDS[raw["severity"]]
    assert d["kind"] == raw["kind"]
    assert d["kind_label"] == {"phrase": "Phrase found", "missing": "Missing text"}[raw["kind"]]


def test_expected_names_and_severity_words_for_the_seven_rules():
    got = {r.id: (describe(flag_for(r))["name"], describe(flag_for(r))["severity_label"]) for r in all_rules()}
    assert got["R1"] == ("No guaranteed-approval claims", "High")
    assert got["R3"] == ("Equal Housing Lender statement", "High")
    assert got["R4"][1] == "Medium" and got["R6"][1] == "Low" and got["R7"][1] == "Medium"


def test_no_text_field_is_ever_empty_for_a_known_rule():
    for r in all_rules():
        d = describe(flag_for(r))
        for key in ("name", "explanation", "snippet", "severity_label", "kind_label", "rule_id"):
            assert isinstance(d[key], str) and d[key].strip(), (r.id, key)


def test_values_are_plain_str_and_bool_only():
    for r in all_rules():
        for v in describe(flag_for(r)).values():
            assert type(v) in (str, bool)


def test_a_removed_rule_gives_a_safe_placeholder_not_an_error():
    d = describe(Flag("R99", "high", "missing"))
    assert d["known"] is False and d["name"] == UNKNOWN_RULE_NAME
    assert d["snippet"] == ""  # nothing to offer as a comment snippet
    assert d["explanation"] and d["rule_id"] == "R99"
    assert d["severity_label"] == "High" and d["kind_label"] == "Missing text"


@pytest.mark.parametrize("rule_id", ["", "r1", "R1; DROP TABLE flag", "<script>alert(1)</script>", "R" * 5000, 7, 1.5, True])
def test_odd_rule_ids_are_unknown_not_a_crash(rule_id):
    d = describe({"rule_id": rule_id, "severity": "low", "kind": "phrase"})
    assert d["known"] is False and d["name"] == UNKNOWN_RULE_NAME and d["snippet"] == ""
    assert d["rule_id"] == str(rule_id)  # raw text; the template escapes it


@pytest.mark.parametrize("bad", [None, object(), 5, "R1", [], {}, {"severity": "high"}])
def test_something_without_a_rule_id_raises_type_error(bad):
    with pytest.raises(TypeError):
        describe(bad)


@pytest.mark.parametrize("severity, kind", [(None, None), ("critical", "regex"), ("", ""), ("HIGH", "Phrase")])
def test_unusable_stored_severity_or_kind_falls_back_to_the_rule_for_known_rules(severity, kind):
    d = describe({"rule_id": "R1", "severity": severity, "kind": kind})
    assert d["severity_label"] == "High" and d["kind"] == "phrase"


def test_unusable_severity_on_an_unknown_rule_is_labelled_unknown():
    d = describe({"rule_id": "R99", "severity": "critical", "kind": "regex"})
    assert d["severity_label"] == "Unknown" and d["severity"] == "" and d["kind_label"] == "Unknown"


def test_the_flags_own_severity_wins_over_the_rule_if_the_rule_has_changed():
    d = describe(Flag("R6", "high", "phrase", "x", 0, 1))  # raised as high, rule is low now
    assert d["severity"] == "high" and d["severity_label"] == "High" and d["name"] == "No pressure or false urgency"


def test_html_in_rule_text_stays_raw(monkeypatch):
    base = rules.get_rule("R1")
    evil = rules.Rule("R1", "<b>Name</b>", "a < b & c", base.products, base.channels, "high", "phrase",
                      base.detection, "<script>alert(1)</script>")
    monkeypatch.setattr(rules, "_cache", (evil,))
    d = describe(Flag("R1", "high", "phrase", "x", 0, 1))
    assert d["name"] == "<b>Name</b>" and d["explanation"] == "a < b & c" and d["snippet"] == "<script>alert(1)</script>"


def test_accepts_engine_flags_dicts_and_stored_rows(conn):
    engine = evaluate("loan", "email", "Guaranteed approval, rates as low as 5.99%")
    assert [describe(f)["rule_id"] for f in engine] == ["R1", "R2", "R5"]
    conn.execute("INSERT INTO submission (title, product, channel, status, launch_date, submitted_by, created_at,"
                 " current_version) VALUES ('t','loan','email','new','2026-10-10','m','z',1)")
    conn.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (1, 1, 'c', 'z')")
    conn.execute("INSERT INTO flag (version_id, rule_id, severity, kind) VALUES (1, 'R3', 'high', 'missing')")
    row = conn.execute("SELECT * FROM flag").fetchone()
    assert isinstance(row, sqlite3.Row)
    d = describe(row)
    assert d["name"] == "Equal Housing Lender statement" and d["kind_label"] == "Missing text"
    assert describe(dict(row)) == d


def test_each_call_returns_a_new_dict_and_cannot_change_the_rules():
    f = flag_for(rules.get_rule("R1"))
    a = describe(f)
    a["name"] = "changed"
    a["snippet"] = ""
    b = describe(f)
    assert b["name"] == "No guaranteed-approval claims" and b["snippet"]
    assert a is not b


def test_describe_does_not_read_files_each_call(monkeypatch):
    f = flag_for(rules.get_rule("R1"))
    describe(f)
    monkeypatch.setattr(rules, "load_rules", lambda *a: pytest.fail("re-read"))
    describe(f)


def test_every_seeded_flag_can_be_described(conn):
    from datetime import datetime, timezone
    from app.seed import seed_all
    seed_all(conn, datetime(2026, 10, 1, tzinfo=timezone.utc))
    rows = conn.execute("SELECT * FROM flag").fetchall()
    assert len(rows) == 25
    assert all(describe(r)["known"] for r in rows)
