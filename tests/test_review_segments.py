import time
from datetime import datetime, timezone

import pytest

from app.review import load_review, segments
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)


def flag(i, start, end, kind="phrase"):
    return {"id": i, "kind": kind, "start_index": start, "end_index": end}


def joined(copy, flags):
    return "".join(t for t, _ in segments(copy, flags))


def test_seed_versions_reproduce_the_copy(conn):
    seed_all(conn, NOW)
    checked = 0
    for sid in range(1, 15):
        for v in load_review(conn, sid)["version_numbers"]:
            d = load_review(conn, sid, v)
            pieces = segments(d["version"]["copy"], d["flags"])
            assert "".join(t for t, _ in pieces) == d["version"]["copy"]
            ids = {i for _, f in pieces for i in f}
            assert ids == {f["id"] for f in d["flags"] if f["kind"] == "phrase"}
            for text, fids in pieces:  # a flagged piece is exactly the stored matched text
                for fid in fids:
                    f = next(x for x in d["flags"] if x["id"] == fid)
                    if len(fids) == 1:
                        assert text == f["matched_text"]
            checked += 1
    assert checked == 16


def test_item_1_r1_piece_keeps_capitalization(conn):
    seed_all(conn, NOW)
    d = load_review(conn, 1)
    flagged = [t for t, f in segments(d["version"]["copy"], d["flags"]) if f]
    assert "Guaranteed approval" in flagged


def test_item_8_three_r6_pieces_in_order_with_text_between(conn):
    seed_all(conn, NOW)
    d = load_review(conn, 8)
    r6 = {f["id"] for f in d["flags"] if f["rule_id"] == "R6"}
    pieces = segments(d["version"]["copy"], d["flags"])
    hits = [i for i, (t, f) in enumerate(pieces) if set(f) & r6]
    assert len(hits) == 3
    assert all(hits[i + 1] - hits[i] >= 2 for i in range(2))  # unflagged text between


def test_no_flags_gives_one_piece():
    assert segments("hello", []) == [("hello", ())]
    assert segments("hello", [flag(1, 0, 5, kind="missing")]) == [("hello", ())]


def test_whole_copy_flagged_and_empty_copy():
    assert segments("hello", [flag(1, 0, 5)]) == [("hello", (1,))]
    assert segments("", []) == []
    assert segments("", [flag(1, 0, 1)]) == []


def test_pieces_around_a_flag():
    assert segments("abcdef", [flag(7, 2, 4)]) == [("ab", ()), ("cd", (7,)), ("ef", ())]


def test_overlap_gives_three_pieces():
    assert segments("0123456789abcdefghij", [flag("a", 0, 10), flag("b", 5, 15)]) == [
        ("01234", ("a",)), ("56789", ("a", "b")), ("abcde", ("b",)), ("fghij", ())]


def test_identical_spans_share_one_piece():
    assert segments("abcdef", [flag("a", 1, 3), flag("b", 1, 3)]) == [
        ("a", ()), ("bc", ("a", "b")), ("def", ())]


def test_adjacent_flags_stay_separate():
    assert segments("abcdefghij", [flag("a", 0, 5), flag("b", 5, 10)]) == [("abcde", ("a",)), ("fghij", ("b",))]


def test_nested_flag_splits_the_outer_one():
    assert segments("abcdefghij", [flag("a", 0, 10), flag("b", 3, 5)]) == [
        ("abc", ("a",)), ("de", ("a", "b")), ("fghij", ("a",))]


def test_flag_order_does_not_change_pieces():
    a, b = flag("a", 0, 6), flag("b", 3, 9)
    assert [t for t, _ in segments("abcdefghi", [a, b])] == [t for t, _ in segments("abcdefghi", [b, a])]


@pytest.mark.parametrize("prefix", ["\U0001F600 ", "é ", "שלום ", "\U0001F600\U0001F600\U0001F600 "])
def test_unicode_before_the_match_does_not_shift_it(prefix):
    copy = prefix + "Guaranteed approval here"
    start = len(prefix)
    assert segments(copy, [flag(1, start, start + 19)]) == [
        (prefix, ()), ("Guaranteed approval", (1,)), (" here", ())]


@pytest.mark.parametrize("bad", [
    flag(1, -1, 3), flag(1, 3, 3), flag(1, 5, 2), flag(1, 0, 99), flag(1, "0", 3), flag(1, 0, 3.0),
    flag(1, None, 3), flag(1, True, 3), {"id": 1}, {"kind": "phrase", "start_index": 0, "end_index": 3},
    None, "x", 5,
])
def test_bad_flags_are_ignored(bad):
    assert segments("abcdef", [bad]) == [("abcdef", ())]
    assert segments("abcdef", [bad, flag(2, 1, 2)]) == [("a", ()), ("b", (2,)), ("cdef", ())]


def test_large_input_is_fast():
    copy = "ab " * 33334  # 100,002 characters
    flags = [flag(i, i * 90, i * 90 + 5) for i in range(1000)]
    t = time.perf_counter()
    pieces = segments(copy, flags)
    assert time.perf_counter() - t < 0.5
    assert "".join(x for x, _ in pieces) == copy
    overlapping = [flag(i, i, i + 5000) for i in range(1000)]
    t = time.perf_counter()
    segments(copy, overlapping)
    assert time.perf_counter() - t < 2
