import re
from collections import Counter
from difflib import SequenceMatcher

# Word-level diff of two copies, as plain data. Pure: no database, clock or network, and it never
# builds HTML (the template wraps each piece and escapes it). Moved text is not detected: a moved
# paragraph shows as removed in one place and added in the other.

MAX_TOKENS = 6_000  # per side; beyond this the page falls back to the plain copy
# SequenceMatcher is quadratic on texts made of a few words repeated thousands of times. When the
# number of candidate word pairs in the changed middle passes this, that middle is shown as one
# removed piece and one added piece instead (coarse, but still exact and always fast).
MAX_PAIRS = 1_000_000

# A token is a run of non-space characters (punctuation stays attached to its word, so "APR." and
# "APR" differ) or a run of whitespace (so a spacing change is visible). Python splits on code
# points, never inside one.
_TOKEN = re.compile(r"\s+|\S+")


def _tokens(text):
    return _TOKEN.findall(text)


def _is_space(token):
    return token.isspace()


def _pairs(a, b):
    """How many (old word, new word) pairs are equal: the work SequenceMatcher would have to do."""
    counts = Counter(t for t in b if not t.isspace())
    return sum(counts[t] for t in a if not t.isspace())


def diff_text(old, new):
    """Compare two texts. Returns an ordered list of (kind, text), kind "same", "added" or "removed".

    Joining the "same" and "removed" pieces gives `old` exactly, and joining "same" and "added" gives
    `new` exactly. Neighbouring pieces of one kind are merged. Returns None, without comparing,
    when either side has more than MAX_TOKENS tokens ("too long to compare").
    """
    if not isinstance(old, str) or not isinstance(new, str):
        raise TypeError("diff_text compares two strings")
    if old == new:
        return [("same", old)] if old else []
    a, b = _tokens(old), _tokens(new)
    if len(a) > MAX_TOKENS or len(b) > MAX_TOKENS:
        return None
    pieces = []
    # Text that is the same at the start and the end needs no comparing, which also keeps the
    # common case (a few edits in a long copy) fast.
    start = 0
    while start < len(a) and start < len(b) and a[start] == b[start]:
        start += 1
    end = 0
    while end < len(a) - start and end < len(b) - start and a[-1 - end] == b[-1 - end]:
        end += 1
    head, tail = a[:start], a[len(a) - end:]
    a, b = a[start:len(a) - end], b[start:len(b) - end]

    def add(kind, tokens):
        text = "".join(tokens)
        if not text:
            return
        if pieces and pieces[-1][0] == kind:
            pieces[-1] = (kind, pieces[-1][1] + text)
        else:
            pieces.append((kind, text))

    # Spaces are "junk" so they never anchor a match (a text of 3,000 single spaces would make the
    # comparison very slow), but a match still grows across equal spaces next to a matched word.
    add("same", head)
    if _pairs(a, b) > MAX_PAIRS:
        add("removed", a)
        add("added", b)
    else:
        matcher = SequenceMatcher(_is_space, a, b, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                add("same", a[i1:i2])
            else:
                add("removed", a[i1:i2])
                add("added", b[j1:j2])
    add("same", tail)
    return pieces


def count_words(pieces, kind):
    """How many words (not spaces) are in the pieces of one kind."""
    return sum(len(p.split()) for k, p in pieces if k == kind)
