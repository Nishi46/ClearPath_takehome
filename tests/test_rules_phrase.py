import time

import pytest

from app.rules import compile_phrase, find_phrase, normalize


def spans(phrase, text):
    return find_phrase(normalize(text), compile_phrase(phrase))


def hits(phrase, text):
    return [text[a:b] for a, b in spans(phrase, text)]


@pytest.mark.parametrize("text", [
    "GUARANTEED APPROVAL", "Guaranteed approval", "guaranteed approval",
    "guaranteed  approval", "guaranteed\napproval", "guaranteed-approval",
    "guaranteed, approval", "guaranteed – approval", "guaranteed\u00a0approval",
])
def test_case_punctuation_and_spacing_match(text):
    assert hits("guaranteed approval", f"Get {text} today") == [text]


@pytest.mark.parametrize("text", [
    "guaranteedapproval", "guaranteed      approval", "guaranteed ... . approval",
    "guaranteed approvals", "guaranteed approve", "guarantee approval",
])
def test_things_that_must_not_match(text):
    assert hits("guaranteed approval", text) == []


def test_gap_limit_is_exactly_three():
    assert len(spans("guaranteed approval", "guaranteed . approval")) == 1  # " . " is 3 characters
    assert len(spans("guaranteed approval", "guaranteed ... approval")) == 0  # 5 characters
    assert spans("guaranteed approval", "guaranteed .. approval") == []  # 4 characters


def test_word_boundaries():
    assert hits("act now", "react now") == []
    assert hits("act now", "exact now") == []
    assert hits("act now", "ACT NOW!!!") == ["ACT NOW"]
    assert hits("act now", "(act now)") == ["act now"]
    assert hits("act now", "now act now") == ["act now"]
    assert hits("hurry", "hurrying along") == []
    assert hits("hurry", "Hurry, spots fill") == ["Hurry"]
    assert hits("hurry", "unhurry") == []
    assert hits("hurry", "_hurry_") == ["hurry"]
    assert hits("hurry", "hurry2") == []


@pytest.mark.parametrize("text", [
    "pre-approved", "Pre-Approved", "pre approved", "pre–approved", "preapproved", "PREAPPROVED",
])
def test_hyphenated_phrase_also_matches_closed_up_and_spaced(text):
    assert hits("pre-approved", text) == [text]


def test_hyphenated_phrase_boundary():
    assert hits("pre-approved", "unpre-approved") == []
    assert hits("pre-approved", "pre-approvedly") == []


@pytest.mark.parametrize("text", ["you're approved", "You’re approved", "YOU’RE APPROVED",
                                  "youre approved", "you re approved"])
def test_apostrophes(text):
    assert hits("you're approved", text) == [text]


def test_cant_variants():
    for t in ("can't be denied", "Can’t be denied", "cant be denied"):
        assert hits("can't be denied", t) == [t]
    assert hits("can't be denied", "can be denied") == []


def test_curly_apostrophe_in_the_phrase_itself():
    assert hits("you’re approved", "You're approved") == ["You're approved"]


def test_multiple_occurrences_in_order_without_overlap():
    text = "Hurry! hurry, hurry hurry"
    found = spans("hurry", normalize(text))
    assert [text[a:b] for a, b in found] == ["Hurry", "hurry", "hurry", "hurry"]
    assert found == sorted(found)
    assert all(found[i][1] <= found[i + 1][0] for i in range(len(found) - 1))


def test_adjacent_matches_do_not_merge():
    text = "act now act now"
    assert hits("act now", text) == ["act now", "act now"]
    assert spans("act now", "act nowact now") == []


def test_overlapping_candidates_are_reported_once():
    assert hits("aa bb aa", "aa bb aa bb aa") == ["aa bb aa"]


def test_spans_slice_the_original_after_emoji_accents_and_rtl():
    text = "\U0001f600é́ שלום \u200b GUARANTEED APPROVAL!"
    (a, b), = spans("guaranteed approval", text)
    assert text[a:b] == "GUARANTEED APPROVAL"
    assert len(text) == len(normalize(text))


def test_invisible_characters_cannot_hide_a_phrase():
    for t in ("guaran\u00adteed approval", "guaranteed\u200b approval", "guaranteed \u200b\u200b approval",
              "g\u200buaranteed approval", "guaranteed\ufeff \u200dapproval"):
        assert len(spans("guaranteed approval", t)) == 1, repr(t)
    t = "guaranteed \u200b approval"
    (a, b), = spans("guaranteed approval", t)
    assert (a, b) == (0, len(t))


def test_invisible_characters_do_not_count_toward_the_gap_limit():
    assert len(spans("guaranteed approval", "guaranteed" + "\u200b" * 50 + " approval")) == 1
    assert len(spans("guaranteed approval", "guaranteed" + " \u200b" * 3 + "approval")) == 1
    assert spans("guaranteed approval", "guaranteed" + " \u200b" * 4 + "approval") == []
    # invisible characters alone are not a separator: this reads as "guaranteedapproval"
    assert spans("guaranteed approval", "guaranteed\u200b\u200dapproval") == []


def test_regex_metacharacters_in_a_phrase_are_literal():
    for phrase in ("a.b*c(", "(guaranteed|approval)", "[a-z]+", "a{2,3}", "\\d+ now", "x|y"):
        pat = compile_phrase(phrase)  # never raises re.error
        assert find_phrase(normalize("aXbbbc guaranteed 1234 now aa"), pat) == []
    # punctuation in a phrase is not required in the text, only the words are
    assert hits("a.b", "a b") == ["a b"]
    assert hits("(guaranteed|approval)", "guaranteed approval") == ["guaranteed approval"]
    assert hits("x|y", "xzy") == []


def test_bad_phrases():
    for bad in ("", "   ", "!!!", "-'-"):
        with pytest.raises(ValueError):
            compile_phrase(bad)
    for bad in (None, 5, b"x", ["x"]):
        with pytest.raises(TypeError):
            compile_phrase(bad)


def test_empty_and_unmatched_text():
    pat = compile_phrase("act now")
    assert find_phrase("", pat) == []
    assert find_phrase(normalize("nothing here"), pat) == []


@pytest.mark.parametrize("text", [
    "a-" * 50_000, "guaranteed " * 9_000, "-" * 100_000, "guaranteed" + "\u200b" * 100_000,
    "guaranteed " + "\u200b" * 100_000 + " approval", "guaranteed approval " * 5_000,
    ("g\u200b" * 50_000), " " * 100_000, "guaranteed" + " ." * 40_000,
])
def test_no_catastrophic_backtracking(text):
    pats = [compile_phrase(p) for p in ("guaranteed approval", "pre-approved", "you're approved", "act now")]
    start = time.perf_counter()
    norm = normalize(text)
    for p in pats:
        find_phrase(norm, p)
    assert time.perf_counter() - start < 1.0
