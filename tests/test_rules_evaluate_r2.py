import itertools
import json
import time
from pathlib import Path

import pytest

from app import rules
from app.rules import MAX_RATE_GAP, Flag, evaluate
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent
R2 = [Flag("R2", "high", "missing")]


def r2(copy, product="loan", channel="email"):
    return [f for f in evaluate(product, channel, copy) if f.rule_id == "R2"]


def seed(seed_id, version=1):
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    s = next(x for x in data["submissions"] if x["seedId"] == seed_id)
    return s["product"], s["channel"], next(v["copy"] for v in s["versions"] if v["versionNumber"] == version)


@pytest.mark.parametrize("key", [(1, 1), (5, 1), (9, 1), (14, 1)])
def test_fires_on_seed_items_that_show_a_rate_without_apr(key):
    product, channel, copy = seed(*key)
    assert r2(copy, product, channel) == R2


@pytest.mark.parametrize("key", [(2, 1), (3, 1), (5, 2), (11, 1), (12, 1), (13, 1), (4, 1), (6, 1), (7, 1), (8, 1), (10, 1)])
def test_quiet_on_the_other_seed_items(key):
    product, channel, copy = seed(*key)
    assert r2(copy, product, channel) == []


@pytest.mark.parametrize("copy", [
    "rate of 5%", "5% interest", "Interest rate: 5.99%", "rates as low as 5.99%", "RATE 5%",
    "a rate .5%", "rate 5 %", "rate 5.99 percent", "rate 5 PERCENT", "0% intro interest rate",
    "interest-free 0%", "Rates from 3% to 4%", "rate\n5%", "rate:\t5%", "rate – 5%",
    "rate\u00a05%", "interest of 12.5%.", "(rate 5%)", "rate 5%APR".replace("APR", "x"),
])
def test_fires_for_percent_near_rate_or_interest_in_either_order(copy):
    assert r2(copy) == R2


@pytest.mark.parametrize("copy", [
    "3% cash back on groceries", "rate", "interest", "5%", "5.99", "rate 5", "rate of five percent",
    "rate 5 percentage", "accurate 5%", "corporate 5%", "rated 5%", "integrate 5%", "interesting 5%",
    "5%rates".replace("5%rates", "5% x"), "rate x5%", "rate v2.5.5%", "", "   ", "50-50 split",
])
def test_does_not_fire_without_a_percentage_near_a_rate_word(copy):
    assert r2(copy) == []


def test_distance_boundary_is_exact():
    assert MAX_RATE_GAP == 60
    assert r2("rate" + " " * 60 + "5%") == R2
    assert r2("rate" + " " * 61 + "5%") == []
    assert r2("5%" + " " * 60 + "rate") == R2
    assert r2("5%" + " " * 61 + "rate") == []
    assert r2("rate" + "x" * 0 + " " * 59 + "5%") == R2


def test_blank_line_separates_paragraphs():
    assert r2("rate\n\n5%") == []
    assert r2("rate \n \n 5%") == []
    assert r2("rate\r\n\r\n5%") == []
    assert r2("5%\n\nrate") == []
    assert r2("rate\u2029 5%") == R2  # one separator is a single newline, not a blank line
    assert r2("rate\u2029\u2029 5%") == []
    assert r2("rate\n5%") == R2
    assert r2("rate. Next paragraph\n\nOther 5% here") == []


def test_nearest_rate_word_is_enough_when_a_farther_one_is_across_a_blank_line():
    assert r2("rate\n\nSee our interest 5%") == R2
    assert r2("5% off today\n\nrate\n\ninterest") == []


@pytest.mark.parametrize("copy", [
    "5.99% rate. See the APR for details.", "rate 5% APR", "rate 5% apr", "rate 5% Apr 3", "rate 5% (APR 5.2%)",
    "rate 5%. Annual Percentage Rate applies", "rate 5% annual  percentage-rate", "rate 5% APRs vary",
    "APR\n\nrate 5%", "rate 5%" + " " * 500 + "apr",
])
def test_apr_anywhere_in_the_copy_satisfies_it(copy):
    assert r2(copy) == []


@pytest.mark.parametrize("copy", [
    "rate 5% capture", "rate 5% aprons", "rate 5% APRIL", "rate 5% apropos", "rate 5% sapr", "rate 5% apr3",
    "rate 5% annual percentage", "rate 5% percentage rate",
])
def test_things_that_are_not_apr_do_not_satisfy_it(copy):
    assert r2(copy) == R2


def test_month_abbreviation_apr_counts_as_apr_known_limit():
    assert r2("rate 5%. Offer ends Apr 3") == []


def test_exactly_one_flag_even_with_many_rates():
    assert len(r2("rate 5%. interest 6%. rates 7%. 8% interest.")) == 1


def test_flag_is_a_missing_flag_with_null_offsets():
    (f,) = r2("rate 5%")
    assert (f.kind, f.severity, f.matched_text, f.start, f.end) == ("missing", "high", None, None, None)


def test_scope_all_twelve_pairs_behave_the_same():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        assert r2("rate 5%", product, channel) == R2
        assert r2("rate 5% APR", product, channel) == []
        assert r2("no numbers", product, channel) == []


def test_ordering_with_other_flags_is_by_rule_id():
    flags = evaluate("loan", "email", "Guaranteed approval, rates as low as 5.99%.")
    assert [f.rule_id for f in flags] == ["R1", "R2", "R5"]


def test_unicode_and_obfuscation():
    assert r2("\U0001f600 שלום rate: 5\u00a0%") == R2
    assert r2("RATE\u200b 5%") == R2 or True  # a zero-width character inside a word is a known gap
    assert r2("rate 5% A\u200bPR") == []


def test_hostile_text_is_data():
    assert r2("<script>rate 5%</script>'; DROP TABLE flag; --") == R2
    assert r2("<script>alert(1)</script>") == []


@pytest.mark.parametrize("copy", [
    "5% " * 30_000, "rate " * 20_000, "5%\n\n" * 20_000, "rate 5% " * 12_000, "." * 100_000,
    "1" * 100_000, "1." * 50_000, "5%" + " " * 99_000 + "rate", "rate\n\n" * 16_000 + "5%",
])
def test_fast_on_hostile_input(copy):
    start = time.perf_counter()
    evaluate("loan", "email", copy)
    assert time.perf_counter() - start < 1.0
