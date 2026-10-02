import dataclasses
import itertools
import json
import time
from pathlib import Path

import pytest

from app import rules
from app.rules import Flag, evaluate
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent
R1_PHRASES = ["guaranteed approval", "everyone is approved", "no credit check", "can't be denied"]


def r1(product, channel, copy):
    return [f for f in evaluate(product, channel, copy) if f.rule_id == "R1"]


def seed_copy(seed_id, version=1):
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    s = next(x for x in data["submissions"] if x["seedId"] == seed_id)
    return s["product"], s["channel"], next(v["copy"] for v in s["versions"] if v["versionNumber"] == version)


def test_seed_1_fires_r1_with_original_capitalization_and_offsets():
    product, channel, copy = seed_copy(1)
    (f,) = r1(product, channel, copy)
    assert f == Flag("R1", "high", "phrase", "Guaranteed approval", copy.index("Guaranteed approval"),
                     copy.index("Guaranteed approval") + len("Guaranteed approval"))
    assert copy[f.start:f.end] == f.matched_text


@pytest.mark.parametrize("phrase", R1_PHRASES)
def test_each_phrase_fires_alone_in_clean_copy(phrase):
    copy = f"Our offer is simple. {phrase.capitalize()} for you. Thanks."
    (f,) = r1("loan", "email", copy)
    assert f.matched_text == phrase.capitalize()
    assert copy[f.start:f.end] == f.matched_text
    assert evaluate("loan", "email", copy) == (f,)  # nothing else evaluated in this step


@pytest.mark.parametrize("copy", [
    "A money-back guarantee on our service.", "We guarantee satisfaction.", "Guaranteed delivery.",
    "Your approval matters.", "Approval takes minutes.", "Everyone is welcome to apply.",
    "Check your credit score for free.", "No credit impact to check rates.", "You can be denied or approved.",
    "", "   ",
])
def test_near_misses_do_not_fire(copy):
    assert r1("loan", "email", copy) == []


def test_fires_on_every_product_and_channel():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        (f,) = r1(product, channel, "Enjoy guaranteed approval today.")
        assert f.severity == "high" and f.kind == "phrase"


def test_two_phrases_give_two_flags_in_order():
    copy = "No credit check! Also, Guaranteed Approval, and again guaranteed approval."
    flags = r1("card", "display", copy)
    assert [f.matched_text for f in flags] == ["No credit check", "Guaranteed Approval", "guaranteed approval"]
    assert [f.start for f in flags] == sorted(f.start for f in flags)
    for f in flags:
        assert copy[f.start:f.end] == f.matched_text


def test_seed_13_explanatory_use_still_fires():
    product, channel, copy = seed_copy(13)
    flags = r1(product, channel, copy)
    assert len(flags) == 2  # the engine reads words, not intent; a reviewer dismisses it
    assert all(f.matched_text.lower() == "guaranteed approval" for f in flags)


def test_obfuscated_and_curly_forms_fire_with_valid_offsets():
    copy = "Hi \U0001f600 שלום can’t be denied, and guaran­teed APPROVAL!"
    flags = r1("loan", "email", copy)
    assert len(flags) == 2
    assert flags[0].matched_text == "can’t be denied"
    assert flags[1].matched_text == "guaran­teed APPROVAL"
    for f in flags:
        assert copy[f.start:f.end] == f.matched_text


def test_overlapping_matches_report_once(monkeypatch):
    # "no credit check" and "credit check approval" overlap on "credit check".
    base = rules.get_rule("R1")
    detection = {"phrases": ["no credit check", "credit check approval"]}
    fake = dataclasses.replace(base, detection=detection)
    monkeypatch.setattr(rules, "_cache", (fake,))
    rules._phrase_patterns.cache_clear()
    try:
        flags = evaluate("loan", "email", "Apply: no credit check approval today")
        assert len(flags) == 1 and flags[0].matched_text == "no credit check"
    finally:
        rules._phrase_patterns.cache_clear()


def test_flag_is_immutable_and_hashable():
    f = Flag("R1", "high", "phrase", "x", 0, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.start = 5
    assert len({f, Flag("R1", "high", "phrase", "x", 0, 1)}) == 1


@pytest.mark.parametrize("kwargs", [
    dict(kind="phrase"),                                                   # no text or offsets
    dict(kind="phrase", matched_text="", start=0, end=1),
    dict(kind="phrase", matched_text="x", start=3, end=3),                  # empty span
    dict(kind="phrase", matched_text="x", start=-1, end=1),
    dict(kind="phrase", matched_text="x", start=0, end="1"),
    dict(kind="phrase", matched_text="x", start=True, end=2),               # bool is not an offset
    dict(kind="phrase", matched_text=5, start=0, end=1),
    dict(kind="missing", matched_text="x"),
    dict(kind="missing", start=0),
    dict(kind="regex"),
    dict(kind="phrase", severity="critical", matched_text="x", start=0, end=1),
])
def test_flag_rejects_shapes_the_schema_would_reject(kwargs):
    kwargs.setdefault("severity", "high")
    with pytest.raises(ValueError):
        Flag("R1", **kwargs)


def test_missing_flag_shape_is_valid():
    assert Flag("R3", "high", "missing").start is None


def test_guards_still_apply():
    with pytest.raises(ValueError):
        evaluate("crypto", "email", "guaranteed approval")
    with pytest.raises(TypeError):
        evaluate("loan", "email", None)
    with pytest.raises(ValueError):
        evaluate("loan", "email", "a" * 100_001)


def test_hostile_input_is_data_not_code():
    copy = "<script>alert(1)</script> '; DROP TABLE flag; -- no credit check"
    (f,) = r1("loan", "email", copy)
    assert f.matched_text == "no credit check"
    copy = "no credit check <script>"
    assert evaluate("loan", "email", copy)[0].matched_text == "no credit check"


def test_same_input_same_output_and_fast_on_long_copy():
    copy = "Guaranteed approval. " * 4000
    start = time.perf_counter()
    first = evaluate("loan", "email", copy)
    assert time.perf_counter() - start < 1.0
    assert first == evaluate("loan", "email", copy)
    assert len(first) == 4000
