import itertools
import json
import time
from pathlib import Path

import pytest

from app import rules
from app.rules import Flag, MAX_COPY_CHARS, evaluate
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent
KITCHEN_SINK = ("Guaranteed approval and no credit check! You're approved, pre-approved even. Rates from "
                "5.99%. Act now, last chance, hurry!")


def ids(flags):
    return [f.rule_id for f in flags]


def test_returns_a_tuple_and_the_same_output_every_time():
    first = evaluate("mortgage", "display", KITCHEN_SINK)
    assert isinstance(first, tuple)
    assert first == evaluate("mortgage", "display", KITCHEN_SINK)
    assert all(isinstance(f, Flag) for f in first)


def test_order_is_rule_id_then_position_with_missing_flags_in_place():
    flags = evaluate("mortgage", "display", KITCHEN_SINK)
    assert ids(flags) == ["R1", "R1", "R2", "R3", "R4", "R4", "R6", "R6", "R6", "R7"]
    for rid in {"R1", "R4", "R6"}:
        starts = [f.start for f in flags if f.rule_id == rid]
        assert starts == sorted(starts)


def test_no_duplicates():
    flags = evaluate("loan", "display", KITCHEN_SINK)
    assert len(flags) == len(set(flags))


def test_order_does_not_depend_on_the_order_of_rules_json(monkeypatch):
    expected = evaluate("mortgage", "display", KITCHEN_SINK)
    monkeypatch.setattr(rules, "_cache", tuple(reversed(rules.all_rules())))
    assert evaluate("mortgage", "display", KITCHEN_SINK) == expected


def test_numeric_not_alphabetical_rule_order(monkeypatch):
    base = rules.get_rule("R3")  # a missing-text rule that always fires on empty mortgage copy
    r10 = rules.Rule("R10", *(getattr(base, f) for f in ("name", "description", "products", "channels",
                     "severity", "kind", "detection", "snippet_text")))
    monkeypatch.setattr(rules, "_cache", (r10, base))
    rules._required_patterns.cache_clear()
    try:
        assert ids(evaluate("mortgage", "email", "")) == ["R3", "R10"]
    finally:
        rules._required_patterns.cache_clear()


@pytest.mark.parametrize("copy", ["", " ", "   \n\t  ", "\n\n"])
def test_empty_and_whitespace_copy_gives_exactly_the_applicable_missing_rules(copy):
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        expected = []
        if product == "mortgage":
            expected.append("R3")
        if product in ("loan", "card"):
            expected.append("R5")
        if channel in ("paid_social", "display"):
            expected.append("R7")
        flags = evaluate(product, channel, copy)
        assert ids(flags) == sorted(expected), (product, channel)
        assert all(f.kind == "missing" for f in flags)


def test_long_and_pathological_copy_is_fast():
    for copy in ("word " * 19_000, "you're approved " * 6_000, "Guaranteed approval. " * 4_500):
        start = time.perf_counter()
        flags = evaluate("loan", "email", copy)
        assert time.perf_counter() - start < 2.0
        assert flags
    assert len([f for f in evaluate("loan", "email", "you're approved " * 6_000) if f.rule_id == "R4"]) == 6_000


def test_copy_at_the_limit_is_accepted_and_one_over_is_refused():
    evaluate("loan", "email", "a" * MAX_COPY_CHARS)
    with pytest.raises(ValueError):
        evaluate("loan", "email", "a" * (MAX_COPY_CHARS + 1))


TORTURE = [
    "\U0001f600 " * 500 + "guaranteed approval",
    "שלום العربية guaranteed approval ש",
    "é" * 200 + " no credit check " + "́" * 50,
    "guaran​teed‍ approval, you’re approved, pre‑approved",
    "\x00guaranteed approval\x00", "﻿﻿guaranteed approval", "İ ẞ ß GUARANTEED APPROVAL",
    "a" * 50 + "" * 50 + "guaranteed approval", "퟿\U0010ffff guaranteed approval",
    "guaranteed approval", "guaranteed  approval",
]


@pytest.mark.parametrize("copy", TORTURE)
def test_unicode_torture_never_crashes_and_spans_slice_back(copy):
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        for f in evaluate(product, channel, copy):
            if f.kind == "phrase":
                assert copy[f.start:f.end] == f.matched_text


@pytest.mark.parametrize("copy", [
    "<script>alert(1)</script> guaranteed approval", "'; DROP TABLE flag; -- guaranteed approval",
    "{{7*7}} ${jndi:ldap://x} guaranteed approval", "%s %d {0} guaranteed approval", "\\x00 \\u0000 guaranteed approval",
    "<img src=x onerror=alert(1)> no credit check",
])
def test_hostile_text_is_only_ever_data(copy):
    flags = evaluate("loan", "email", copy)
    phrase = [f for f in flags if f.kind == "phrase"]
    assert len(phrase) == 1 and phrase[0].rule_id == "R1"
    assert copy[phrase[0].start:phrase[0].end] == phrase[0].matched_text
    assert "<" not in phrase[0].matched_text


@pytest.mark.parametrize("args", [
    ("crypto", "email", "x"), ("loan", "fax", "x"), (None, "email", "x"), ("loan", None, "x"),
    ("<script>", "email", "x"), ("loan", "'; DROP TABLE flag; --", "x"), ("Loan", "email", "x"),
])
def test_bad_product_or_channel_raises_before_anything_runs(args, monkeypatch):
    monkeypatch.setattr(rules, "normalize", lambda t: pytest.fail("ran after a bad argument"))
    with pytest.raises(ValueError):
        evaluate(*args)


@pytest.mark.parametrize("copy", [None, b"guaranteed approval", 5, ["x"], object()])
def test_non_string_copy_raises_type_error(copy):
    with pytest.raises(TypeError):
        evaluate("loan", "email", copy)


def test_an_error_inside_a_rule_propagates_instead_of_returning_a_clean_result(monkeypatch):
    def boom(norm):
        raise RuntimeError("rule failed")
    monkeypatch.setattr(rules, "_TRIGGERS", {"R2": boom})
    with pytest.raises(RuntimeError):
        evaluate("loan", "email", "Guaranteed approval, rate 5%")
    monkeypatch.setattr(rules, "find_phrase", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        evaluate("loan", "email", "Guaranteed approval")


def test_evaluate_does_not_change_its_inputs_or_the_rules():
    before = rules.all_rules()
    copy = "Guaranteed approval"
    evaluate("loan", "email", copy)
    assert rules.all_rules() is before and copy == "Guaranteed approval"


def test_every_flag_is_consistent_with_its_rule_and_the_copy():
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    copies = [(s["product"], s["channel"], v["copy"]) for s in data["submissions"] for v in s["versions"]]
    copies += [(p, c, KITCHEN_SINK) for p, c in itertools.product(PRODUCTS, CHANNELS)]
    known = {r.id: r for r in rules.all_rules()}
    for product, channel, copy in copies:
        for f in evaluate(product, channel, copy):
            rule = known[f.rule_id]
            assert f.severity == rule.severity and f.kind == rule.kind
            assert product in rule.products and channel in rule.channels
            if f.kind == "phrase":
                assert 0 <= f.start < f.end <= len(copy)
                assert copy[f.start:f.end] == f.matched_text
            else:
                assert (f.matched_text, f.start, f.end) == (None, None, None)


def test_flags_are_valid_for_the_database_shape():
    # Every flag can be built only if it satisfies the same rules as the flag table's CHECK.
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        for f in evaluate(product, channel, KITCHEN_SINK):
            Flag(f.rule_id, f.severity, f.kind, f.matched_text, f.start, f.end)


def test_threads_get_identical_results():
    from concurrent.futures import ThreadPoolExecutor
    expected = evaluate("mortgage", "display", KITCHEN_SINK)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: evaluate("mortgage", "display", KITCHEN_SINK), range(64)))
    assert all(r == expected for r in results)
