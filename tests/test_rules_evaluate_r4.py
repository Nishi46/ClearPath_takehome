import itertools
import json
from pathlib import Path

import pytest

from app import rules
from app.rules import evaluate
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent


def r4(product, channel, copy):
    return [f for f in evaluate(product, channel, copy) if f.rule_id == "R4"]


def seed_copy(seed_id, version=1):
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    s = next(x for x in data["submissions"] if x["seedId"] == seed_id)
    return s["product"], s["channel"], next(v["copy"] for v in s["versions"] if v["versionNumber"] == version)


@pytest.mark.parametrize("seed_id, expected", [
    (3, "You're approved"),
    (8, "approved in minutes"),
    (12, "pre-approved"),
])
def test_seed_items_fire_r4_with_exact_text(seed_id, expected):
    product, channel, copy = seed_copy(seed_id)
    (f,) = r4(product, channel, copy)
    assert f.matched_text.lower() == expected.lower()
    assert f.severity == "medium" and f.kind == "phrase"
    assert copy[f.start:f.end] == f.matched_text


def test_seed_12_fires_r4_as_its_only_flag():
    product, channel, copy = seed_copy(12)
    assert [f.rule_id for f in evaluate(product, channel, copy)] == ["R4"]


@pytest.mark.parametrize("phrase", ["you're approved", "pre-approved", "approved in minutes"])
def test_each_phrase_fires_alone_for_mortgage_and_loan(phrase):
    for product, channel in itertools.product(("mortgage", "loan"), CHANNELS):
        (f,) = r4(product, channel, f"Good news: {phrase} for the next step.")
        assert f.matched_text == phrase


def test_r4_never_fires_for_card_whatever_the_copy():
    copy = "You're approved! Pre-approved in minutes, approved in minutes."
    for channel in CHANNELS:
        assert r4("card", channel, copy) == []


@pytest.mark.parametrize("copy", [
    "approved", "Your approval is pending.", "Get approved with a few clicks.", "You are approved",
    "pre-qualified", "Prequalify in minutes.", "See if you prequalify.", "unapproved", "disapproved in minutes",
    "approved minutes later", "Your loan was approved in two minutes.", "pre-approve",
])
def test_near_misses_do_not_fire(copy):
    assert r4("mortgage", "email", copy) == []


@pytest.mark.parametrize("copy, text", [
    ("YOU'RE APPROVED today", "YOU'RE APPROVED"),
    ("Hi \U0001f600 You’re approved!", "You’re approved"),
    ("Youre approved!", "Youre approved"),
    ("You’re  approved", "You’re  approved"),
    ("preapproved today", "preapproved"),
    ("Pre approved today", "Pre approved"),
    ("approved\nin\nminutes", "approved\nin\nminutes"),
    ("pre‑approved", "pre‑approved"),
])
def test_forgiving_forms_fire_and_slice_the_original(copy, text):
    (f,) = r4("mortgage", "email", copy)
    assert f.matched_text == text
    assert copy[f.start:f.end] == text


def test_r1_and_r4_both_fire_ordered_by_rule_then_position():
    copy = "You're approved! Also, guaranteed approval for everyone. Pre-approved too."
    flags = [f for f in evaluate("loan", "email", copy) if f.kind == "phrase"]
    assert [(f.rule_id, f.matched_text) for f in flags] == [
        ("R1", "guaranteed approval"), ("R4", "You're approved"), ("R4", "Pre-approved")]
    r4_flags = [f for f in flags if f.rule_id == "R4"]
    assert [f.start for f in r4_flags] == sorted(f.start for f in r4_flags)
    for f in flags:
        assert copy[f.start:f.end] == f.matched_text


def test_seed_8_has_r1_and_r4_so_far():
    product, channel, copy = seed_copy(8)
    assert {f.rule_id for f in evaluate(product, channel, copy)} == {"R1", "R4"}


def test_r4_does_not_depend_on_r1_and_hostile_text_is_data():
    copy = "<b>You're approved</b>'; DROP TABLE flag; --"
    (f,) = r4("loan", "display", copy)
    assert f.matched_text == "You're approved"


def test_every_other_pair_scope_is_respected():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        found = r4(product, channel, "You're approved.")
        assert bool(found) == (product in ("mortgage", "loan")), (product, channel)
