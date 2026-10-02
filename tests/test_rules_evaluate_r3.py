import itertools
import json
from pathlib import Path

import pytest

from app import rules
from app.rules import Flag, compile_phrase, evaluate, is_present, normalize
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent


def r3(product, channel, copy):
    return [f for f in evaluate(product, channel, copy) if f.rule_id == "R3"]


def seed_copy(seed_id, version=1):
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    s = next(x for x in data["submissions"] if x["seedId"] == seed_id)
    return s["product"], s["channel"], next(v["copy"] for v in s["versions"] if v["versionNumber"] == version)


@pytest.mark.parametrize("seed_id", [3, 14])
def test_seed_items_missing_the_statement_fire_r3(seed_id):
    product, channel, copy = seed_copy(seed_id)
    assert r3(product, channel, copy) == [Flag("R3", "high", "missing")]


@pytest.mark.parametrize("seed_id", [6, 9, 12])
def test_seed_items_with_the_statement_do_not(seed_id):
    product, channel, copy = seed_copy(seed_id)
    assert r3(product, channel, copy) == []


@pytest.mark.parametrize("text", [
    "Equal Housing Lender", "EQUAL HOUSING LENDER", "equal housing lender.", "Equal  Housing  Lender",
    "Equal\nHousing\nLender", "Equal-Housing-Lender", "NMLS #1. Equal Housing Lender. Loans vary",
    "Equal Housing Lender", "Equal Housing​ Lender", "Equal Hous­ing Lender",
])
def test_present_in_many_forms(text):
    assert r3("mortgage", "email", f"Intro text. {text}") == []


@pytest.mark.parametrize("text", [
    "Equal Housing Opportunity", "Equal Lender", "Housing Lender", "Equal Housing", "Equal Opportunity Lender",
    "Fair Housing Lender", "EqualHousingLender", "Equal Housing Lenders", "unequal housing lender",
    "Equal Housing Lender" [:-3],
])
def test_near_misses_still_fire(text):
    assert r3("mortgage", "email", f"Intro text. {text}") == [Flag("R3", "high", "missing")]


def test_boundary_inside_a_longer_word_does_not_count_as_present():
    assert len(r3("mortgage", "email", "We are an unequal housing lenders group")) == 1


def test_never_fires_for_loan_or_card_even_when_absent():
    for product, channel in itertools.product(("loan", "card"), CHANNELS):
        assert r3(product, channel, "No statement here.") == []


def test_fires_for_mortgage_on_every_channel_when_absent():
    for channel in CHANNELS:
        assert r3("mortgage", channel, "No statement here.") == [Flag("R3", "high", "missing")]


def test_missing_flag_has_no_text_or_offsets():
    (f,) = r3("mortgage", "email", "Nothing.")
    assert (f.matched_text, f.start, f.end) == (None, None, None)
    assert f.kind == "missing" and f.severity == "high"


def test_empty_and_whitespace_copy_on_mortgage_fires():
    assert len(r3("mortgage", "email", "")) == 1
    assert len(r3("mortgage", "email", "   \n ")) == 1


def test_exactly_one_flag_even_if_statement_absent_and_copy_is_long():
    assert len(r3("mortgage", "email", "word " * 20_000)) == 1


def test_statement_buried_at_the_very_end_or_start_counts():
    assert r3("mortgage", "email", "Equal Housing Lender") == []
    assert r3("mortgage", "email", "x " * 5000 + "Equal Housing Lender") == []
    assert r3("mortgage", "email", "Equal Housing Lender " + "x " * 5000) == []


def test_ordering_with_phrase_flags_is_by_rule_id():
    copy = "You're approved! Guaranteed approval!"
    flags = evaluate("mortgage", "email", copy)
    assert [f.rule_id for f in flags] == ["R1", "R3", "R4"]
    assert flags[1].kind == "missing"


def test_phrase_flags_still_work_when_statement_present():
    flags = evaluate("mortgage", "email", "Guaranteed approval. Equal Housing Lender.")
    assert [f.rule_id for f in flags] == ["R1"]


def test_hostile_text_is_data():
    assert r3("mortgage", "email", "<script>Equal Housing Lender</script>'; DROP TABLE flag; --") == []
    assert len(r3("mortgage", "email", "<script>alert(1)</script>")) == 1


def test_is_present_helper():
    pats = (compile_phrase("equal housing lender"),)
    assert is_present(normalize("EQUAL HOUSING LENDER"), pats)
    assert not is_present(normalize("equal housing"), pats)
    assert not is_present("", pats)
    assert not is_present(normalize("anything"), ())
    assert is_present("x", (compile_phrase("nope"), compile_phrase("x")))


def test_full_scope_matrix_for_r3():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        assert bool(r3(product, channel, "nothing")) == (product == "mortgage")
