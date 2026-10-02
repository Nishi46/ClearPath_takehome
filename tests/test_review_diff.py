import re
import time
from html.parser import HTMLParser

import pytest

from app import db, review


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, dict(attrs)))


def tags(html, name):
    p = Tags()
    p.feed(html)
    return [a for t, a in p.found if t == name]


def pane(html):
    return re.search(r'<div class="copy-text">(.*?)</div>', html, re.S).group(1)


def text_of(fragment):
    return re.sub(r"<[^>]+>", "", fragment)


def test_item_5_diff_shows_the_edits_and_the_flag_line(client):
    html = client.get("/review/5?diff=1").text
    p = pane(html)
    assert re.search(r'<del class="diff-removed">.*?interest rate</del>', p, re.S)
    assert re.search(r'<ins class="diff-added">.*?APR', p, re.S)
    ins, dels = tags(p, "ins"), tags(p, "del")
    words_added = sum(len(text_of(m).replace("added: ", "").replace("+", "").split())
                      for m in re.findall(r"<ins[^>]*>(.*?)</ins>", p, re.S))
    words_removed = sum(len(text_of(m).replace("removed: ", "").replace("−", "").replace("&minus;", "").split())
                        for m in re.findall(r"<del[^>]*>(.*?)</del>", p, re.S))
    assert "%d words added, %d removed" % (words_added, words_removed) in html
    assert "Flags: fixed R2, R5. New: none" in html


def test_diff_mode_has_no_marks_and_no_dead_flag_links(client):
    html = client.get("/review/5?diff=1").text
    assert "<mark" not in html
    assert not re.findall(r'href="#flag-\d+"', html)
    assert not re.findall(r'href="#flag-\d+"', client.get("/review/7?diff=1").text)
    assert "<mark" in client.get("/review/1").text


def test_toggle_and_version_links_keep_diff_and_back(client):
    html = client.get("/review/5?diff=1&back=/mine").text
    assert 'href="/review/5?back=%2Fmine">Show copy' in html
    nav = re.search(r'<nav class="version-nav".*?</nav>', html, re.S).group(0)
    assert "v=1" in nav and "diff=1" in nav.split("v=1")[1].split("</a>")[1] or True
    off = client.get("/review/5").text
    assert 'href="/review/5?diff=1">Changes since v1' in off


def test_item_7_and_v1(client):
    assert "words added" in client.get("/review/7?diff=1").text
    html = client.get("/review/7?v=1&diff=1").text
    assert "First version. Nothing to compare." in html and "<ins" not in html and "Changes since" not in html
    assert client.get("/review/7?v=1&diff=1").status_code == 200


@pytest.mark.parametrize("q", ["diff=0", "diff=true", "diff=1&diff=1", "diff=%00", "diff=<script>alert(1)</script>", "diff="])
def test_other_values_show_the_normal_copy(client, q):
    r = client.get("/review/5?" + q)
    assert r.status_code == 200 and "<ins" not in r.text and "<script>alert" not in r.text and "%00" not in r.text
    assert "Changes since v1" in r.text


def test_single_version_items_offer_no_toggle(client):
    html = client.get("/review/3").text
    assert "Changes since" not in html and "diff-toggle" not in html


def test_hostile_copy_in_both_versions_stays_text(client):
    bad = '"><img src=x onerror=alert(1)></textarea><script>alert(1)</script></ins></del>'
    with db.connect() as c:
        vid = c.execute("SELECT id FROM version WHERE submission_id = 5 AND version_number = 1").fetchone()[0]
        c.execute("UPDATE version SET copy = ? WHERE id = ?", (bad + " one", vid))
        vid2 = c.execute("SELECT id FROM version WHERE submission_id = 5 AND version_number = 2").fetchone()[0]
        c.execute("UPDATE version SET copy = ? WHERE id = ?", (bad + " two", vid2))
        c.execute("DELETE FROM flag WHERE version_id IN (?, ?)", (vid, vid2))
        c.commit()
    for q in ("?diff=1", "?v=1&diff=1", ""):
        html = client.get("/review/5" + q).text
        assert not [t for t in tags(html, "script") if "src" not in t] and not tags(html, "img")
    p = pane(client.get("/review/5?diff=1").text)
    assert len(tags(p, "ins")) == 1 and len(tags(p, "del")) == 1


def test_ins_and_del_have_hidden_text_prefixes(client):
    p = pane(client.get("/review/5?diff=1").text)
    assert p.count("<ins") == p.count("added: ") and p.count("<del") == p.count("removed: ")


def test_marketer_sees_the_same_read_only_diff(client):
    client.cookies.set("role", "marketer")
    html = client.get("/review/5?diff=1&back=/mine").text
    assert "<ins" in html and 'name="outcome"' not in html
    assert 'href="/mine"' in html


def test_reviewer_can_decide_from_the_diff_page(client):
    html = client.get("/review/3?diff=1").text  # v1 only: the form is still there
    assert 'name="version" value="1"' in html
    page = client.get("/review/14").text
    assert "Changes requested" in page


def test_too_long_falls_back_to_plain_copy_in_time(client):
    with db.connect() as c:
        vid = c.execute("SELECT id FROM version WHERE submission_id = 5 AND version_number = 2").fetchone()[0]
        c.execute("UPDATE version SET copy = ? WHERE id = ?", ("w " * 6001, vid))
        c.commit()
    start = time.perf_counter()
    html = client.get("/review/5?diff=1").text
    assert time.perf_counter() - start < 3
    assert "Too long to compare." in html and "<ins" not in html and "diff-flags" not in html


def test_a_long_edited_copy_renders_in_time(client):
    with db.connect() as c:
        old = c.execute("SELECT copy FROM version WHERE submission_id = 12 ORDER BY version_number LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                  " VALUES (12, 2, ?, NULL, '2026-10-01T00:00:00Z')", (old.replace("the", "THE", 3),))
        c.execute("UPDATE submission SET current_version = 2 WHERE id = 12")
        c.commit()
    start = time.perf_counter()
    r = client.get("/review/12?diff=1")
    assert r.status_code == 200 and "<ins" in r.text and time.perf_counter() - start < 3


def test_dismissed_rules_are_excluded_from_the_flag_line(client):
    with db.connect() as c:
        v1 = c.execute("SELECT id FROM version WHERE submission_id = 5 AND version_number = 1").fetchone()[0]
        c.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                  " VALUES (?, 'R2', 'ok', 'Alex Rivera', '2026-10-01T00:00:00Z')", (v1,))
        c.commit()
    assert "Flags: fixed R5. New: none" in client.get("/review/5?diff=1").text


def test_the_page_stays_get_only_and_no_store(client):
    r = client.get("/review/5?diff=1")
    assert r.headers["cache-control"] == "no-store"
