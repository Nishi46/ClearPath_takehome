"""The engine's output for every seeded version, checked against seed-data.md section 2.

The expected flags live here, as plain data, and not in data/seed.json: flags are computed from
the copy, never stored in the seed, so a rule or copy edit shows up as a failing row below.
"""
import json
from collections import Counter
from pathlib import Path

import pytest

from app import rules
from app.rules import evaluate

ROOT = Path(rules.__file__).resolve().parent.parent

# (seedId, version) -> rule ids that must fire (one entry per rule, whatever the occurrence count).
EXPECTED = {
    (1, 1): {"R1", "R2", "R5"},
    (2, 1): {"R5", "R7"},
    (3, 1): {"R3", "R4"},
    (4, 1): {"R1", "R7"},
    (5, 1): {"R2", "R5"},
    (5, 2): set(),
    (6, 1): set(),
    (7, 1): {"R5"},
    (7, 2): set(),
    (8, 1): {"R1", "R4", "R6"},
    (9, 1): {"R2"},
    (10, 1): set(),
    (11, 1): set(),
    (12, 1): {"R4"},
    (13, 1): {"R1"},
    (14, 1): {"R2", "R3", "R7"},
}

# Phrase rules: how many occurrences are flagged (each is highlighted separately in phase 4).
EXPECTED_PHRASE_COUNTS = {
    (1, 1): {"R1": 1},
    (3, 1): {"R4": 1},
    (4, 1): {"R1": 1},
    (8, 1): {"R1": 2, "R4": 1, "R6": 3},
    (12, 1): {"R4": 1},
    (13, 1): {"R1": 2},
}

SEED = json.loads((ROOT / "data" / "seed.json").read_text())
VERSIONS = {
    (s["seedId"], v["versionNumber"]): (s["product"], s["channel"], v["copy"], s["title"])
    for s in SEED["submissions"] for v in s["versions"]
}


def describe_mismatch(key, got, want):
    missing, extra = sorted(want - got), sorted(got - want)
    title = VERSIONS[key][3]
    parts = [f"Seed #{key[0]} v{key[1]} ({title!r}):"]
    if missing:
        parts.append(f"expected but did not fire: {', '.join(missing)}")
    if extra:
        parts.append(f"fired but not expected: {', '.join(extra)}")
    return " ".join(parts)


def flags_for(key):
    product, channel, copy, _ = VERSIONS[key]
    return evaluate(product, channel, copy)


@pytest.mark.parametrize("key", sorted(EXPECTED), ids=lambda k: f"seed{k[0]}-v{k[1]}")
def test_flags_match_the_expected_table_exactly(key):
    got = {f.rule_id for f in flags_for(key)}
    assert got == EXPECTED[key], describe_mismatch(key, got, EXPECTED[key])


def test_the_table_covers_every_seeded_version_and_nothing_else():
    assert set(EXPECTED) == set(VERSIONS), (
        f"seed versions without an expected row: {sorted(set(VERSIONS) - set(EXPECTED))}; "
        f"rows without a seed version: {sorted(set(EXPECTED) - set(VERSIONS))}")
    assert len(VERSIONS) == 16 and len({k[0] for k in VERSIONS}) == 14
    assert {k for k in VERSIONS if k[0] in (5, 7)} == {(5, 1), (5, 2), (7, 1), (7, 2)}


@pytest.mark.parametrize("key", sorted(EXPECTED_PHRASE_COUNTS), ids=lambda k: f"seed{k[0]}-v{k[1]}")
def test_phrase_occurrence_counts(key):
    got = Counter(f.rule_id for f in flags_for(key) if f.kind == "phrase")
    assert dict(got) == EXPECTED_PHRASE_COUNTS[key], f"seed #{key[0]} v{key[1]}"


def test_every_rule_fires_somewhere_and_stays_quiet_on_an_in_scope_item():
    fired = {key: {f.rule_id for f in flags_for(key)} for key in VERSIONS}
    for rule in rules.all_rules():
        fires_on = [k for k, ids in fired.items() if rule.id in ids]
        quiet_on = [k for k, (p, c, _, _) in VERSIONS.items()
                    if p in rule.products and c in rule.channels and rule.id not in fired[k]]
        assert fires_on, f"{rule.id} fires on no seed item"
        assert quiet_on, f"{rule.id} fires on every in-scope seed item, so its negative side is untested"


def test_near_misses_from_the_seed_do_not_fire():
    assert "money-back guarantee" in VERSIONS[(10, 1)][2]
    assert "R1" not in {f.rule_id for f in flags_for((10, 1))}
    assert "limited time: offer ends Oct 31" in VERSIONS[(11, 1)][2]
    assert "R6" not in {f.rule_id for f in flags_for((11, 1))}


def test_known_recall_limit_seed_8_everyone_gets_a_yes_is_not_caught():
    copy = VERSIONS[(8, 1)][2]
    at = copy.index("everyone gets a yes")
    for f in flags_for((8, 1)):
        if f.kind == "phrase":
            assert not (f.start <= at < f.end), f"{f.rule_id} unexpectedly covers 'everyone gets a yes'"
            assert "yes" not in f.matched_text.lower()


def test_seed_8_phrase_texts_and_offsets():
    copy = VERSIONS[(8, 1)][2]
    found = [(f.rule_id, f.matched_text) for f in flags_for((8, 1)) if f.kind == "phrase"]
    assert found == [("R1", "Guaranteed approval"), ("R1", "no credit check"), ("R4", "approved in minutes"),
                     ("R6", "Act now"), ("R6", "last chance"), ("R6", "Hurry")]
    for f in flags_for((8, 1)):
        if f.kind == "phrase":
            assert copy[f.start:f.end] == f.matched_text


def test_seed_13_false_positive_fires_both_mentions_for_the_reviewer_to_dismiss():
    found = [f.matched_text.lower() for f in flags_for((13, 1)) if f.kind == "phrase"]
    assert found == ["guaranteed approval", "guaranteed approval"]


@pytest.mark.parametrize("key", [(6, 1), (10, 1)])
def test_clean_items_have_no_flags_at_all(key):
    assert flags_for(key) == ()


def test_seed_12_is_long_and_fires_exactly_r4():
    copy = VERSIONS[(12, 1)][2]
    assert len(copy.split()) >= 500
    assert [f.rule_id for f in flags_for((12, 1))] == ["R4"]
    (f,) = flags_for((12, 1))
    assert f.matched_text.lower() == "pre-approved"


def test_severity_kind_and_missing_shape_match_the_rule_file_for_every_flag():
    known = {r.id: r for r in rules.all_rules()}
    for key in VERSIONS:
        for f in flags_for(key):
            assert (f.severity, f.kind) == (known[f.rule_id].severity, known[f.rule_id].kind), key
            if f.kind == "missing":
                assert (f.matched_text, f.start, f.end) == (None, None, None), key


def test_removing_a_fix_changes_the_result_so_the_table_is_not_vacuous():
    # #5 v2 is clean because APR and the disclaimer were added; take them out and R2 and R5 return.
    product, channel, copy, _ = VERSIONS[(5, 2)]
    broken = copy.replace("APR", "rate").replace("subject to credit approval", "subject to terms")
    assert {f.rule_id for f in evaluate(product, channel, broken)} >= {"R2", "R5"}


def test_mismatch_message_names_the_submission_and_rule():
    msg = describe_mismatch((8, 1), {"R1", "R2"}, {"R1", "R6"})
    assert "Seed #8 v1" in msg and "R6" in msg and "R2" in msg
    assert "expected but did not fire: R6" in msg and "fired but not expected: R2" in msg


def test_total_flag_counts_match_the_expected_table():
    total_rules = sum(len(ids) for ids in EXPECTED.values())
    got = sum(len({f.rule_id for f in flags_for(k)}) for k in VERSIONS)
    assert got == total_rules == 21
