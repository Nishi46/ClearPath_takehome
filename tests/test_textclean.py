import pytest

from app.textclean import clean_text


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n", "​", "﻿", " ", " ", "\u200F",
                                 "\x00", " ​\t\n "])
def test_invisible_only_is_none(raw):
    assert clean_text(raw) is None


def test_visible_text_is_trimmed_and_kept():
    assert clean_text("  ok  ") == "ok"
    assert clean_text("a\n\tb") == "a\n\tb"
    assert clean_text("​ ok ​") == "​ ok ​".strip()


@pytest.mark.parametrize("raw", [["x"], 5, b"x", object()])
def test_non_strings_raise(raw):
    with pytest.raises(TypeError):
        clean_text(raw)


def test_emoji_and_combining_characters_survive():
    assert clean_text("é \U0001F600") == "é \U0001F600"
