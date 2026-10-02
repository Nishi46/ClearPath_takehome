import re
from pathlib import Path

import pytest

from app import notes
from app.notes import MESSAGES, validate_note


def test_boundaries():
    assert validate_note("x") == ("x", None)
    assert validate_note("x" * 1000) == ("x" * 1000, None)
    assert validate_note("x" * 1001) == (None, "note_too_long")
    assert validate_note("\U0001F600" * 1000)[1] is None  # characters, not bytes
    assert validate_note("\U0001F600" * 1001)[1] == "note_too_long"


def test_length_counts_after_trimming():
    assert validate_note("  " + "x" * 1000 + "  ")[1] is None


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", "​", None, ["a"], b"a", object(), 5])
def test_blank_or_non_string_is_required(raw):
    assert validate_note(raw) == (None, "note_required")


@pytest.mark.parametrize("raw", ["a\x00b", "\x00", "a\x07", "\x1b[31m", "x\x7f", "a\x85b"])
def test_control_characters_refused(raw):
    assert validate_note(raw) == (None, "note_bad_chars")


def test_tab_and_newline_kept_and_crlf_normalized():
    assert validate_note("a\tb\nc") == ("a\tb\nc", None)
    assert validate_note("a\r\nb\rc") == ("a\nb\nc", None)


def test_messages_are_fixed_and_never_echo_input():
    assert set(MESSAGES) == set(notes.CODES)
    for raw in ("SECRET-\x00", "SECRET" * 300):
        _, code = validate_note(raw)
        assert "SECRET" not in MESSAGES[code]


def test_module_is_pure():
    src = Path(notes.__file__).read_text()
    for banned in ("conn", "datetime", "now(", "sqlite3", "import time", "requests"):
        assert not re.search(r"\b%s" % re.escape(banned), src), banned
