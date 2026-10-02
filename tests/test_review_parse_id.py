import re
from pathlib import Path

import pytest

from app.review import parse_id


@pytest.mark.parametrize("raw, expected", [("3", 3), ("000003", 3), ("0", 0), ("999999999", 999999999)])
def test_valid(raw, expected):
    assert parse_id(raw) == expected


@pytest.mark.parametrize("raw", [
    "", " 3", "3 ", "+3", "-3", "3.0", "0x3", "1e3", "３", "٣", "3\n", "\n3", "3\x00",
    "1" * 10, "1 OR 1=1", "1; DROP TABLE submission", "../1", "%00", "\u202E3",
    None, 3, 3.0, b"3", ["3"], True,
])
def test_invalid_returns_none(raw):
    assert parse_id(raw) is None


def test_no_sql_text_is_assembled():
    source = Path("app/review.py").read_text()
    assert not re.search(r'f["\'].*(SELECT|INSERT|UPDATE|DELETE)', source, re.I)
    assert not re.search(r'["\'].*(SELECT|INSERT|UPDATE|DELETE).*["\']\s*%', source, re.I)
