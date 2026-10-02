import re
from html.parser import HTMLParser

import pytest

from app import db


class Pane(HTMLParser):
    """Collects the copy pane's own text (skipping the flag tags) and counts elements inside it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip = 0
        self.text = []
        self.marks = []
        self.tags = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "div" and "copy-text" in (a.get("class") or ""):
            self.depth = 1
            return
        if not self.depth:
            return
        self.depth += 1
        self.tags.append(tag)
        if tag == "mark":
            self.marks.append(a)
        if self.skip or "flag-tag" in (a.get("class") or ""):
            self.skip += 1

    def handle_endtag(self, tag):
        if not self.depth:
            return
        self.depth -= 1
        if self.skip:
            self.skip -= 1
        if tag in self.tags:
            pass

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.text.append(data)


def pane(html):
    p = Pane()
    p.feed(html)
    return p


def set_copy(sid, copy):
    with db.connect() as c:
        c.execute("UPDATE version SET copy = ? WHERE submission_id = ? AND version_number = "
                  "(SELECT current_version FROM submission WHERE id = ?)", (copy, sid, sid))


def reflag(sid):
    from app.flags import evaluate_version, store_flags
    with db.connect() as c:
        vid = c.execute("SELECT v.id FROM version v JOIN submission s ON s.id = v.submission_id"
                        " AND v.version_number = s.current_version WHERE s.id = ?", (sid,)).fetchone()[0]
        store_flags(c, vid, evaluate_version(c, vid))


def stored_copy(sid):
    with db.connect() as c:
        return c.execute("SELECT v.copy FROM version v JOIN submission s ON s.id = v.submission_id"
                         " AND v.version_number = s.current_version WHERE s.id = ?", (sid,)).fetchone()[0]


def test_item_1_has_one_labeled_mark_with_letter(client):
    r = client.get("/review/1")
    p = pane(r.text)
    assert p.text and "Guaranteed approval" in "".join(p.text)
    assert len([m for m in p.marks if "mark-high" in m["class"]]) >= 1
    mark = re.search(r"<mark[^>]*>Guaranteed approval(.*?)</mark>", r.text, re.S)
    assert mark
    assert "High severity flag: R1" in mark.group(1)       # accessible name
    assert re.search(r'>H</span>', mark.group(1))          # visible letter, not color only


@pytest.mark.parametrize("sid", range(1, 15))
def test_pane_text_equals_the_stored_copy(client, sid):
    p = pane(client.get(f"/review/{sid}").text)
    assert "".join(p.text) == stored_copy(sid)


def test_long_item_keeps_line_breaks_and_blank_lines(client):
    copy = stored_copy(12)
    assert "\n\n" in copy
    assert "".join(pane(client.get("/review/12").text).text) == copy


def test_html_in_copy_is_text_not_markup(client):
    evil = 'x <b>bold</b> &amp; "q" \'s\' ` <script>alert(1)</script> </mark><mark> end'
    set_copy(6, evil)
    r = client.get("/review/6")
    p = pane(r.text)
    assert "".join(p.text) == evil
    assert p.tags == [] and p.marks == []                  # no elements created from the copy
    assert "<script>alert" not in r.text and "<b>bold" not in r.text


def test_copy_with_a_phrase_and_markup_keeps_structure(client):
    set_copy(1, "<b>Guaranteed approval</b> </mark> now")
    reflag(1)
    p = pane(client.get("/review/1").text)
    assert len(p.marks) == 1 and p.tags.count("mark") == 1
    assert "<b>" not in "".join(p.tags)


def test_clean_item_has_no_marks(client):
    p = pane(client.get("/review/6").text)
    assert p.marks == [] and p.tags == []


def test_anchor_ids_are_unique_and_match_flags(client):
    for sid in range(1, 15):
        ids = [i for i in pane(client.get(f"/review/{sid}").text).ids if i.startswith("flag-")]
        assert len(ids) == len(set(ids))
    with db.connect() as c:
        n = c.execute("SELECT count(*) FROM flag f JOIN version v ON v.id=f.version_id WHERE v.submission_id=8 "
                      "AND f.kind='phrase'").fetchone()[0]
    assert len([i for i in pane(client.get("/review/8").text).ids if i.startswith("flag-")]) == n


def test_item_8_has_three_r6_marks_plus_others(client):
    p = pane(client.get("/review/8").text)
    assert len(p.marks) >= 3


def test_overlapping_flags_keep_every_anchor(client):
    with db.connect() as c:
        vid = c.execute("SELECT id FROM version WHERE submission_id = 6").fetchone()[0]
        c.execute("UPDATE version SET copy = 'abcdefghij' WHERE id = ?", (vid,))
        c.execute("DELETE FROM flag WHERE version_id = ?", (vid,))
        for sev, s, e in (("high", 0, 6), ("medium", 3, 9)):
            c.execute("INSERT INTO flag (version_id, rule_id, severity, kind, matched_text, start_index, end_index)"
                      " VALUES (?, 'R1', ?, 'phrase', 'x', ?, ?)", (vid, sev, s, e))
    p = pane(client.get("/review/6").text)
    assert "".join(p.text) == "abcdefghij"
    ids = [i for i in p.ids if i.startswith("flag-")]
    assert len(ids) == 2 == len(set(ids))


def test_dismissed_rule_is_not_highlighted(client):
    r = client.get("/review/13")                            # R1 dismissed in the seed
    p = pane(r.text)
    assert p.marks == [] and "".join(p.text) == stored_copy(13)


def test_unknown_severity_does_not_crash(client):
    with db.connect() as c:
        c.execute("PRAGMA ignore_check_constraints = ON")
        c.execute("UPDATE flag SET severity = 'weird' WHERE id = (SELECT min(id) FROM flag WHERE kind = 'phrase')")
    for sid in range(1, 15):
        assert client.get(f"/review/{sid}").status_code == 200


def test_notes_shown_escaped_or_empty(client):
    with db.connect() as c:
        c.execute("UPDATE version SET notes = ? WHERE submission_id = 3", ("<img src=x onerror=alert(1)>\nline2",))
        c.execute("UPDATE version SET notes = NULL WHERE submission_id = 6")
    r = client.get("/review/3")
    assert "<img" not in r.text and "&lt;img" in r.text and "Notes from marketer" in r.text
    assert "No notes." in client.get("/review/6").text
    with db.connect() as c:
        c.execute("UPDATE version SET notes = '   ' WHERE submission_id = 6")
    assert "No notes." in client.get("/review/6").text


def test_css_wraps_long_words_and_keeps_line_breaks():
    css = open("app/static/style.css").read()
    assert "white-space: pre-wrap; overflow-wrap: anywhere" in css
    assert "prefers-reduced-motion" in css


def test_long_unbroken_string_renders(client):
    set_copy(6, "x" * 5000)
    assert "".join(pane(client.get("/review/6").text).text) == "x" * 5000
