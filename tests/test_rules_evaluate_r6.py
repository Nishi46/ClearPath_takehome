import itertools
import json
import time
from pathlib import Path

import pytest

from app import rules
from app.rules import MAX_END_DATE_GAP, evaluate
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent


def r6(copy, product="loan", channel="email"):
    return [f for f in evaluate(product, channel, copy) if f.rule_id == "R6"]


def seed(seed_id, version=1):
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    s = next(x for x in data["submissions"] if x["seedId"] == seed_id)
    return s["product"], s["channel"], next(v["copy"] for v in s["versions"] if v["versionNumber"] == version)


def test_seed_8_fires_for_all_three_phrases_in_order():
    product, channel, copy = seed(8)
    flags = r6(copy, product, channel)
    assert [f.matched_text for f in flags] == ["Act now", "last chance", "Hurry"]
    assert all(f.severity == "low" and f.kind == "phrase" for f in flags)
    assert [f.start for f in flags] == sorted(f.start for f in flags)
    for f in flags:
        assert copy[f.start:f.end] == f.matched_text


def test_seed_11_near_miss_is_suppressed_by_its_end_date():
    product, channel, copy = seed(11)
    assert "limited time" in copy.lower()
    assert r6(copy, product, channel) == []


@pytest.mark.parametrize("key", [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (5, 2), (6, 1), (7, 1), (7, 2),
                                 (9, 1), (10, 1), (12, 1), (13, 1), (14, 1)])
def test_no_other_seed_item_fires_r6(key):
    product, channel, copy = seed(*key)
    assert r6(copy, product, channel) == []


@pytest.mark.parametrize("phrase", ["act now", "last chance", "hurry", "limited time"])
def test_each_phrase_fires_alone(phrase):
    copy = f"A fine offer. {phrase.upper()}! Thanks."
    (f,) = r6(copy)
    assert f.matched_text == phrase.upper()


@pytest.mark.parametrize("text", [
    "Offer ends Oct 31", "offer ends Oct. 31st", "expires 10/31", "valid through December 1", "until Jan 5",
    "ends 10/31/2026", "ends 1/5/27", "ends on or before Oct 31", "expires 31 October", "ends 1st of May",
    "Ends SEPT 5", "thru Nov 30", "ending Dec 2nd", "ends: October 31, 2026", "EXPIRES JUNE 3",
    "ends\nOct 31", "ends  Oct  31", "ends Oct 31",
])
def test_stated_end_dates_suppress_r6(text):
    assert r6(f"Act now, last chance! {text}") == []
    assert r6(f"{text}. Act now, hurry, limited time.") == []  # before or after the phrases


@pytest.mark.parametrize("text", [
    "ends soon", "ends today", "ends 31", "ends Octopus 31", "until you're ready", "ends Oct",
    "ends Oct 32", "ends 13/1", "ends 10/32", "ends Oct31", "Posted Oct 31", "ends 3/", "ends Octob 31",
    "ends Marching 5", "until Mayhem 5", "ends 10.31", "ends 10-31", "ends in 5 days", "on Oct 31",
])
def test_things_that_are_not_an_end_date_do_not_suppress(text):
    assert len(r6(f"Act now! {text}")) == 1


def test_limited_time_ends_soon_still_fires_both_phrases():
    assert [f.matched_text for f in r6("Act now! limited time, ends soon")] == ["Act now", "limited time"]


def test_gap_between_end_word_and_date_is_at_most_twenty():
    assert MAX_END_DATE_GAP == 20
    assert r6("Act now. ends" + " " * 20 + "Oct 31") == []
    assert len(r6("Act now. ends" + " " * 21 + "Oct 31")) == 1


def test_known_limit_a_nearby_unrelated_date_after_an_end_word_still_suppresses():
    assert r6("Act now. Offer expires soon. Posted Oct 31") == []


@pytest.mark.parametrize("copy", [
    "react now", "exact now", "ACT NOW!!!", "(act now)", "reaction now", "hurrying", "unhurried",
    "last chances", "limited times", "Limited-time offer", "limited  time",
])
def test_phrase_boundaries_and_forgiving_forms(copy):
    expected = copy in ("ACT NOW!!!", "(act now)", "Limited-time offer", "limited  time")
    assert bool(r6(copy)) == expected


def test_other_rules_still_run_when_r6_is_suppressed():
    flags = evaluate("loan", "email", "Guaranteed approval! Limited time: ends Oct 31. Subject to credit approval.")
    assert [f.rule_id for f in flags] == ["R1"]


def test_scope_all_pairs():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        assert len(r6("Hurry!", product, channel)) == 1
        assert r6("Hurry! Ends Oct 31", product, channel) == []


def test_ordering_with_other_flags():
    flags = evaluate("loan", "display", "Hurry! Guaranteed approval.")
    assert [f.rule_id for f in flags] == ["R1", "R5", "R6", "R7"]


def test_unicode_and_hostile_text():
    copy = "\U0001f600 שלום HURRY"
    (f,) = r6(copy)
    assert copy[f.start:f.end] == "HURRY"
    assert len(r6("<script>hurry</script>'; DROP TABLE flag; --")) == 1


@pytest.mark.parametrize("copy", [
    "ends " * 20_000, "until " * 16_000, "ends 1" * 16_000, "ends" + " " * 99_000, "ends oct " * 11_000,
    "ends " + "oct " * 24_000, "hurry " * 16_000, "10/" * 33_000,
])
def test_fast_on_hostile_input(copy):
    start = time.perf_counter()
    evaluate("loan", "email", copy)
    assert time.perf_counter() - start < 1.0
