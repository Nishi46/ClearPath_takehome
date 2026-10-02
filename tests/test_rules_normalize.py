import re
import random
import string
import time
from pathlib import Path

import pytest

from app import rules
from app.rules import IGNORABLE, normalize

SAMPLES = [
    "", " ", "Plain text", "Emoji \U0001f600 and \U0001f468\u200d\U0001f469\u200d\U0001f467",
    "café é combining", "Straße STRASSE", "İstanbul İ", "שלום العربية RTL",
    "a\u00a0b\u2003c\u3000d", "x\u2028y\u2029z\u0085w", "null\x00byte", "퟿\ue000\uf8ff",
    "ﬁ ligature ẞ ΣΣ final sigma", "tab\tnew\nline\r\n",
]


def test_length_is_preserved_for_awkward_strings():
    for s in SAMPLES:
        assert len(normalize(s)) == len(s), repr(s)


def test_length_is_preserved_for_random_strings():
    rng = random.Random(1234)
    pool = (string.printable + "ßİ’–\u200b\u00ad\u00a0\u3000\U0001f600א́"
            + "".join(chr(c) for c in range(0x80, 0x500)))
    for _ in range(300):
        s = "".join(rng.choice(pool) for _ in range(rng.randint(0, 200)))
        assert len(normalize(s)) == len(s)


def test_length_is_preserved_for_100k_chars():
    s = ("Guaranteed ’ İ \u200b\U0001f600 " * 6000)[:100_000]
    start = time.perf_counter()
    out = normalize(s)
    assert len(out) == len(s) == 100_000
    assert time.perf_counter() - start < 0.5


def test_case_and_curly_punctuation():
    assert normalize("You’re APPROVED") == "you're approved"
    assert normalize("you’re") == normalize("you're")
    assert normalize("Pre–approved") == "pre-approved"
    for dash in "‐‑‒–—―−﹣－":
        assert normalize(f"a{dash}b") == "a-b"
    assert normalize("“Hi”") == '"hi"'


def test_unicode_spaces_become_spaces_but_newlines_stay():
    assert normalize("a\u00a0b\u2003c\u3000d\u202fe") == "a b c d e"
    assert normalize("a\nb\r\nc\td") == "a\nb\r\nc\td"
    assert normalize("a\u2028b\u2029c\u0085d") == "a\nb\nc\nd"


def test_invisible_characters_are_neutralized_not_removed():
    s = "guaran\u00adteed approval"
    out = normalize(s)
    assert out == f"guaran{IGNORABLE}teed approval"
    assert len(out) == len(s)
    for ch in "\u200b\u200c\u200d\u2060\ufeff\u00ad\u200E\u202E":
        assert normalize(f"a{ch}b") == f"a{IGNORABLE}b"


def test_special_lowercase_cases_keep_length():
    assert normalize("İ") == "i"
    assert normalize("ß") == "ß"
    assert normalize("ẞ") == "ß"
    assert len(normalize("İ" * 10)) == 10


def test_idempotent():
    for s in SAMPLES + ["Pre–approved \u200b you’re"]:
        assert normalize(normalize(s)) == normalize(s), repr(s)


def test_emoji_and_rtl_do_not_shift_offsets():
    s = "\U0001f600 שלום GUARANTEED APPROVAL"
    n = normalize(s)
    i = n.index("guaranteed approval")
    assert s[i:i + 19] == "GUARANTEED APPROVAL"


def test_empty_and_whitespace_only():
    assert normalize("") == ""
    assert normalize("   \n\t ") == "   \n\t "


@pytest.mark.parametrize("bad", [None, b"bytes", 5, ["a"], ("a",), object()])
def test_non_string_raises_type_error(bad):
    with pytest.raises(TypeError):
        normalize(bad)


def test_null_byte_and_markup_pass_through_untouched():
    assert normalize("a\x00b") == "a\x00b"
    assert normalize("<SCRIPT>'; DROP TABLE flag; --") == "<script>'; drop table flag; --"


def test_source_has_no_dynamic_code():
    src = Path(rules.__file__).read_text()
    for banned in ("eval(", "exec(", "pickle", "yaml.load"):
        assert banned not in src
    assert not re.search(r"(?<![\w.])compile\(", src)  # re.compile is fine, builtin compile is not
