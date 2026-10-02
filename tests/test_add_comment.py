import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, review
from app.notes import COMMENT_CODES, COMMENT_MESSAGES, validate_comment
from app.review import CommentError, add_comment
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 2, 9, 5, 0, tzinfo=timezone.utc)
ME = "Alex Rivera"
TEXT = "Please add the APR next to the rate."


@pytest.fixture
def seeded(conn):
    seed_all(conn, NOW)
    return conn


def rows(c):
    return tuple(tuple(tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t))
                 for t in ("submission", "version", "flag", "flag_dismissal", "decision", "comment"))


def comment(c, sid=3, v=1, text=TEXT, rule=None, who=ME, now=LATER):
    return add_comment(c, sid, v, text, rule, who, now)


def refused(c, code, *a, **k):
    before = rows(c)
    with pytest.raises(CommentError) as e:
        comment(c, *a, **k)
    assert e.value.code == code
    assert rows(c) == before
    assert not c.in_transaction


# ---- validation (pure) ----

def test_comment_text_boundaries_and_rules():
    assert validate_comment("x") == ("x", None)
    assert validate_comment("x" * 2000)[1] is None and validate_comment("x" * 2001) == (None, "text_too_long")
    assert validate_comment("\U0001F600" * 2000)[1] is None  # characters, not bytes
    for blank in ("", "  ", "\t\n", "​", None, ["x"], b"x"):
        assert validate_comment(blank) == (None, "text_required")
    for bad in ("a\x00", "\x07", "\x1b[0m", "\x7f"):
        assert validate_comment(bad) == (None, "text_bad_chars")
    assert validate_comment("a\r\nb\rc\td") == ("a\nb\nc\td", None)
    assert set(COMMENT_MESSAGES) == set(COMMENT_CODES)
    assert all("SECRET" not in COMMENT_MESSAGES[validate_comment("SECRET\x00")[1]] for _ in (0,))


# ---- happy paths ----

def test_free_comment_on_3(seeded):
    out = comment(seeded, text="  " + TEXT + "  ")
    row = seeded.execute("SELECT * FROM comment WHERE id = (SELECT max(id) FROM comment)").fetchone()
    assert (row["submission_id"], row["version_number"], row["author"], row["text"], row["rule_id"],
            row["created_at"]) == (3, 1, ME, TEXT, None, "2026-10-02T09:05:00Z")
    assert out["text"] == TEXT and not seeded.in_transaction
    data = review.load_review(seeded, 3)
    assert data["comments"][-1]["text"] == TEXT


def test_comment_linked_to_a_rule_on_12(seeded):
    assert comment(seeded, 12, 1, "Quoted phrase, check context.", "R4")["rule_id"] == "R4"
    assert seeded.execute("SELECT rule_id FROM comment WHERE submission_id = 12").fetchone()[0] == "R4"


def test_a_dismissed_rule_can_still_be_commented_on(seeded):
    review.dismiss_flag(seeded, 12, 1, "R4", "False positive.", ME, NOW)
    assert comment(seeded, 12, 1, "Dismissed, see note.", "R4")["rule_id"] == "R4"


@pytest.mark.parametrize("sid, v", [(6, 1), (8, 1), (14, 1), (13, 1), (7, 2)])
def test_locked_versions_take_comments_without_changing_anything_else(seeded, sid, v):
    before = rows(seeded)
    comment(seeded, sid, v)
    after = rows(seeded)
    assert after[:5] == before[:5]  # status, versions, flags, dismissals, decisions untouched
    assert len(after[5]) == len(before[5]) + 1


# ---- refusals ----

@pytest.mark.parametrize("text, code", [("", "text_required"), ("  ", "text_required"), (None, "text_required"),
                                        ("​⁠", "text_required"), (["x"], "text_required"),
                                        ("x" * 2001, "text_too_long"), ("a\x00b", "text_bad_chars")])
def test_text_rules_write_nothing(seeded, text, code):
    refused(seeded, code, text=text)


@pytest.mark.parametrize("v", [1, 0, -1, 99, None, True, 1.0, "2"])
def test_stale_versions_on_5(seeded, v):
    refused(seeded, "stale_version", 5, v)


@pytest.mark.parametrize("sid", [0, 999, None, "3", True, 3.0])
def test_unknown_submission(seeded, sid):
    refused(seeded, "not_found", sid)


@pytest.mark.parametrize("rule", ["R2", "R99", "", "r4", "R4 ", ["R4"], "R4' OR '1'='1", b"R4", 4])
def test_no_such_flag(seeded, rule):
    refused(seeded, "no_such_flag", 12, 1, rule=rule)


def test_rule_that_did_not_fire_on_this_item_is_refused_even_if_it_exists(seeded):
    refused(seeded, "no_such_flag", 14, 1, rule="R4")


def test_capacity_boundary(seeded):
    have = seeded.execute("SELECT count(*) FROM comment WHERE submission_id = 3").fetchone()[0]
    for i in range(review.MAX_COMMENTS_PER_SUBMISSION - 1 - have):
        seeded.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at)"
                       " VALUES (3, 1, 'x', ?, '2026-10-01T00:00:00Z')", ("c%d" % i,))
    seeded.commit()
    assert comment(seeded, text="the 200th")["text"] == "the 200th"
    refused(seeded, "capacity", text="the 201st")
    comment(seeded, 12, 1, "other items are unaffected")


def test_duplicate_rules(seeded):
    comment(seeded)
    refused(seeded, "duplicate")
    comment(seeded, text="something else")
    comment(seeded)  # the same text after a different comment is allowed
    comment(seeded, who="Someone Else")  # a different author is allowed
    comment(seeded, 12, 1, TEXT, "R4")
    comment(seeded, 12, 1, TEXT, None)  # same text, different rule link, is allowed
    refused(seeded, "duplicate", 12, 1, TEXT, None)


@pytest.mark.parametrize("who", ["", "   ", None, 5])
def test_author_must_be_a_name(seeded, who):
    before = rows(seeded)
    with pytest.raises(ValueError):
        comment(seeded, who=who)
    assert rows(seeded) == before and not seeded.in_transaction


def test_open_transaction_is_refused(seeded):
    seeded.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError):
        comment(seeded)
    seeded.rollback()


def test_a_failing_insert_rolls_back(seeded, monkeypatch):
    from app import seed

    before = rows(seeded)
    monkeypatch.setattr(seed, "format_timestamp", lambda _n: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        comment(seeded)
    assert rows(seeded) == before and not seeded.in_transaction


def test_sql_in_text_is_stored_verbatim(seeded):
    evil = "'); DROP TABLE comment;--"
    comment(seeded, text=evil)
    assert seeded.execute("SELECT text FROM comment WHERE text = ?", (evil,)).fetchone() is not None
    assert seeded.execute("SELECT count(*) FROM comment").fetchone()[0] > 5


# ---- concurrency ----

def _threads(n, fn):
    out, errors = [], []

    def run(i):
        try:
            with db.connect() as c:
                out.append(fn(c, i))
        except CommentError as e:
            out.append(e.code)
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))

    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    return out


def test_identical_comments_at_once_make_one_row(db_path, seeded):
    seeded.commit()
    out = _threads(20, lambda c, i: comment(c) and "ok")
    assert sorted(out) == ["duplicate"] * 19 + ["ok"]
    assert seeded.execute("SELECT count(*) FROM comment WHERE text = ?", (TEXT,)).fetchone()[0] == 1


def test_different_comments_at_once_are_all_kept(db_path, seeded):
    seeded.commit()
    before = seeded.execute("SELECT count(*) FROM comment").fetchone()[0]
    out = _threads(20, lambda c, i: comment(c, text="comment %d" % i) and "ok")
    assert out == ["ok"] * 20
    ids = [r[0] for r in seeded.execute("SELECT id FROM comment WHERE text LIKE 'comment %'")]
    assert len(ids) == 20 == len(set(ids)) and seeded.execute("SELECT count(*) FROM comment").fetchone()[0] == before + 20


def test_statements_use_placeholders_and_comments_are_never_changed():
    src = Path(review.__file__).read_text()
    body = src[src.index("def add_comment"):src.index("COMMENT_FIELD_CODES")]
    for stmt in re.findall(r'"((?:SELECT|INSERT|UPDATE|DELETE)[^"]*)"', body):
        assert "%" not in stmt and "format" not in stmt
    assert not re.search(r"(UPDATE|DELETE FROM)\s+comment\b", src)
