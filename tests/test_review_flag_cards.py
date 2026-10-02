import re
from html.parser import HTMLParser

import pytest

from app import db, queue
from app.rules import describe
from tests.test_review_copy_pane import pane


class Cards(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_cards = self.in_h2 = False
        self.heading = ""
        self.cards = []
        self.cur = None
        self.links = []
        self.field = None
        self.in_occ = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        c = a.get("class") or ""
        if tag == "section" and "flags-pane" in c:
            self.in_cards = True
        if not self.in_cards:
            return
        if tag == "h2":
            self.in_h2 = True
        if tag == "li" and "flag-card" in c:
            self.cur = {"h3": "", "severity": "", "why": "", "occ": [], "html": []}
            self.cards.append(self.cur)
        if self.cur is not None:
            if tag == "h3":
                self.field = "h3"
            elif tag == "p" and "flag-severity" in c:
                self.field = "severity"
            elif tag == "p" and "flag-why" in c:
                self.field = "why"
            elif tag == "ul" and "flag-occurrences" in c:
                self.in_occ = True
            elif tag == "li" and self.in_occ:
                self.field = "occ"
                self.cur["occ"].append({"text": "", "href": None, "title": None})
            if tag in ("a", "span") and self.field == "occ":
                self.cur["occ"][-1]["href"] = a.get("href")
                self.cur["occ"][-1]["title"] = a.get("title")
            if tag in ("script", "img", "b"):
                self.cur["html"].append(tag)

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_h2 = False
        if tag == "section" and self.in_cards:
            self.in_cards = False
        if tag in ("h3", "p", "li"):
            self.field = None
        if tag == "ul":
            self.in_occ = False

    def handle_data(self, data):
        if self.in_h2:
            self.heading += data
        if self.cur is not None and self.field:
            if self.field == "occ":
                self.cur["occ"][-1]["text"] += data
            else:
                self.cur[self.field] += data


def cards(html):
    p = Cards()
    p.feed(html)
    for c in p.cards:
        for o in c["occ"]:
            o["text"] = o["text"].strip()
    return p


def get(client, sid, v=None):
    return cards(client.get(f"/review/{sid}" + (f"?v={v}" if v else "")).text)


def test_item_1_cards_in_severity_order_with_words_and_explanation(client):
    p = get(client, 1)
    ids = [re.match(r"\s*\S+ (R\d)", c["h3"]).group(1) if False else re.search(r"R\d", c["h3"]).group(0) for c in p.cards]
    assert ids == ["R1", "R2", "R5"]
    sev_rank = {"High": 0, "Medium": 1, "Low": 2}
    ranks = [sev_rank[c["severity"].replace("Severity:", "").strip()] for c in p.cards]
    assert ranks == sorted(ranks)
    for c in p.cards:
        rid = re.search(r"R\d", c["h3"]).group(0)
        d = describe({"rule_id": rid, "severity": "high", "kind": "phrase"})
        assert d["name"] in c["h3"]
        assert d["explanation"] in c["why"] and "Why it fired" in c["why"]
        assert re.search(r"Severity: (High|Medium|Low)", c["severity"])  # a word, not only a letter


def test_item_8_r6_is_one_card_with_three_linked_occurrences(client):
    r = client.get("/review/8")
    p = cards(r.text)
    r6 = [c for c in p.cards if "R6" in c["h3"]]
    assert len(r6) == 1 and len(r6[0]["occ"]) == 3
    marks = pane(r.text).ids
    for o in r6[0]["occ"]:
        assert o["href"].startswith("#flag-")
        assert marks.count(o["href"][1:]) == 1               # exactly one target, none dangling


@pytest.mark.parametrize("sid", range(1, 15))
def test_every_href_has_one_target_and_header_matches_the_queue(client, sid):
    r = client.get(f"/review/{sid}")
    ids = pane(r.text).ids
    for href in re.findall(r'href="#(flag-\d+)"', r.text):
        assert ids.count(href) == 1
    with db.connect() as c:
        row = [x for x in queue.list_queue(c) if x["id"] == sid][0]
    assert cards(r.text).heading.strip() == f"Flags ({row['flag_count']})"
    assert len(cards(r.text).cards) == row["flag_count"]


def test_old_version_shows_its_own_cards(client):
    assert len(get(client, 5, 1).cards) == 2 and get(client, 5).cards == []


def test_matched_text_is_escaped_and_long_text_is_truncated(client):
    with db.connect() as c:
        fid = c.execute("SELECT id FROM flag WHERE kind='phrase' ORDER BY id LIMIT 1").fetchone()[0]
        c.execute("UPDATE flag SET matched_text = ? WHERE id = ?", ("<script>alert(1)</script>" + "y" * 500, fid))
        sid = c.execute("SELECT v.submission_id FROM flag f JOIN version v ON v.id=f.version_id WHERE f.id=?", (fid,)).fetchone()[0]
    r = client.get(f"/review/{sid}")
    assert "<script>alert" not in r.text
    occ = [o for c in cards(r.text).cards for o in c["occ"] if o["title"] and o["title"].startswith("<script>")][0]
    assert len(occ["text"]) < 130 and occ["text"].endswith("”") and "…" in occ["text"]
    assert len(occ["title"]) == 525


def test_unknown_rule_shows_the_placeholder(client):
    with db.connect() as c:
        c.execute("UPDATE flag SET rule_id = 'R99' WHERE id = (SELECT min(id) FROM flag WHERE kind='phrase')")
        sid = c.execute("SELECT v.submission_id FROM flag f JOIN version v ON v.id=f.version_id WHERE f.rule_id='R99'").fetchone()[0]
    p = get(client, sid)
    assert any("Rule no longer available" in c["h3"] for c in p.cards)


def test_unknown_severity_on_unknown_rule_renders_unknown(client):
    with db.connect() as c:
        c.execute("PRAGMA ignore_check_constraints = ON")
        c.execute("UPDATE flag SET rule_id = 'R99', severity = '<b>x</b>' WHERE id = (SELECT min(id) FROM flag WHERE kind='phrase')")
        sid = c.execute("SELECT v.submission_id FROM flag f JOIN version v ON v.id=f.version_id WHERE f.rule_id='R99'").fetchone()[0]
    r = client.get(f"/review/{sid}")
    assert "Severity: Unknown" in r.text and "<b>x</b>" not in r.text


def test_dismissed_rules_have_no_card(client):
    assert get(client, 13).cards == []


def test_a_flag_with_a_bad_span_has_no_dangling_link(client):
    with db.connect() as c:
        c.execute("UPDATE flag SET end_index = 99999 WHERE id = (SELECT min(id) FROM flag WHERE kind='phrase')")
    for sid in range(1, 15):
        r = client.get(f"/review/{sid}")
        ids = pane(r.text).ids
        for href in re.findall(r'href="#(flag-\d+)"', r.text):
            assert href in ids
