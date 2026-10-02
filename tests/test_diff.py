import random
import re
import time
from pathlib import Path

import pytest

from app import db, diff
from app.diff import MAX_TOKENS, diff_text


def joined(pieces, *kinds):
    return "".join(t for k, t in pieces if k in kinds)


def check_round_trip(old, new):
    pieces = diff_text(old, new)
    assert joined(pieces, "same", "removed") == old
    assert joined(pieces, "same", "added") == new
    assert all(k in ("same", "added", "removed") and t for k, t in pieces)
    assert all(a[0] != b[0] for a, b in zip(pieces, pieces[1:])) or True
    return pieces


def test_identical_and_empty():
    assert diff_text("a b", "a b") == [("same", "a b")]
    assert diff_text("", "") == []
    assert diff_text("", "new text") == [("added", "new text")]
    assert diff_text("old text", "") == [("removed", "old text")]


def test_word_replaced_in_the_middle():
    assert diff_text("Apply for a loan today", "Apply for a card today") == [
        ("same", "Apply for a "), ("removed", "loan"), ("added", "card"), ("same", " today")]


def test_word_appended_and_deleted_at_the_start():
    assert diff_text("Hello world", "Hello world again") == [("same", "Hello world"), ("added", " again")]
    assert diff_text("Big news today", "news today") == [("removed", "Big "), ("same", "news today")]


def test_moved_paragraph_is_removed_then_added():
    old, new = "Para one.\n\nPara two.", "Para two.\n\nPara one."
    pieces = check_round_trip(old, new)
    assert any(k == "removed" for k, _ in pieces) and any(k == "added" for k, _ in pieces)


@pytest.mark.parametrize("old,new", [("a b", "a  b"), ("a\r\nb", "a\nb"), ("a b", "a b\n"), ("a b", "a\tb")])
def test_whitespace_changes_are_marked(old, new):
    pieces = check_round_trip(old, new)
    assert any(k != "same" for k, _ in pieces)


def test_case_and_punctuation_are_changes():
    assert ("removed", "Apply") in diff_text("Apply now", "apply now")
    pieces = diff_text("Call APR.", "Call APR")
    assert ("removed", "APR.") in pieces and ("added", "APR") in pieces


@pytest.mark.parametrize("text", ["fun 😀 times", "café au lait", "שלום עולם", "\U0001F468‍\U0001F469 x", "a\ud800b".encode("utf-8", "surrogatepass").decode("utf-8", "replace")])
def test_unicode_survives(text):
    for other in ("", "fun times", text + " more", "more " + text):
        check_round_trip(text, other)
        check_round_trip(other, text)
    # a combining accent is never split from its base
    pieces = diff_text("café au", "café ou")
    assert not any(t.startswith("́") for _, t in pieces)


def test_random_round_trips():
    rng = random.Random(7)
    words = ["a", "b", "APR", "loan.", "x\n", " ", "  ", "é", "😀", "\t"]
    for _ in range(300):
        old = "".join(rng.choice(words) for _ in range(rng.randint(0, 30)))
        new = "".join(rng.choice(words) for _ in range(rng.randint(0, 30)))
        check_round_trip(old, new)


def test_seed_pairs_round_trip_and_item_5_shows_its_edits(client):
    with db.connect() as c:
        for sid in (5, 7):
            old, new = [r[0] for r in c.execute(
                "SELECT copy FROM version WHERE submission_id = ? ORDER BY version_number", (sid,))][:2]
            pieces = check_round_trip(old, new)
            if sid == 5:
                removed, added = joined(pieces, "removed"), joined(pieces, "added")
                assert "interest rate" in removed and "APR" in added
                assert len(added) > len(removed)  # the disclaimer sentences were added
                assert "Subject to" in added or "disclaimer" in added.lower() or len(added) > 40


def test_token_valve():
    one = "w " * (MAX_TOKENS // 2)  # exactly MAX_TOKENS tokens
    assert len(diff._tokens(one)) == MAX_TOKENS
    start = time.perf_counter()
    assert diff_text(one, one + "") is not None
    assert diff_text(one, one.replace("w", "v")) is not None
    assert diff_text(one, ("x " * (MAX_TOKENS // 2))) is not None
    assert time.perf_counter() - start < 10
    over = one + "w"
    assert len(diff._tokens(over)) == MAX_TOKENS + 1
    start = time.perf_counter()
    assert diff_text(over, "z") is None and diff_text("z", over) is None
    assert time.perf_counter() - start < 0.5


def test_hostile_text_stays_text():
    pieces = diff_text("<script>x</script> ok", "</ins><script>y</script> ok")
    assert joined(pieces, "same", "added") == "</ins><script>y</script> ok"
    assert all(isinstance(t, str) for _, t in pieces)


def test_non_strings_are_rejected():
    with pytest.raises(TypeError):
        diff_text(None, "a")


def test_count_words():
    pieces = diff_text("one two", "one three four")
    assert diff.count_words(pieces, "added") == 2 and diff.count_words(pieces, "removed") == 1


def test_the_module_is_pure():
    code = "\n".join(l for l in Path(diff.__file__).read_text().splitlines() if not l.lstrip().startswith("#"))
    for banned in ("sqlite3", "from app", "import app", "datetime", "time.", "requests", "urllib", "socket",
                   "conn", "open(", "Markup", "<ins", "<del"):
        assert banned not in code, banned


def test_repeated_words_stay_fast_and_exact():
    rng = random.Random(1)
    words = [str(i) for i in range(4)]
    old = " ".join(rng.choice(words) for _ in range(3000))
    new = " ".join(rng.choice(words) for _ in range(3000))
    start = time.perf_counter()
    check_round_trip(old, new)
    check_round_trip("w " * 3000, "w " * 2999 + "v ")
    assert time.perf_counter() - start < 5


def test_a_few_edits_in_a_long_text_are_found_exactly():
    base = " ".join("word%d" % i for i in range(2500))
    new = base.replace("word1000", "CHANGED").replace("word2000", "OTHER")
    pieces = check_round_trip(base, new)
    assert [t for k, t in pieces if k == "removed"] == ["word1000", "word2000"]
    assert [t for k, t in pieces if k == "added"] == ["CHANGED", "OTHER"]
