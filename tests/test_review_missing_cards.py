import dataclasses
import re

import pytest
from markupsafe import escape

from app import db, rules
from tests.test_review_copy_pane import pane


def snippet_of(rule_id):
    # As it appears in the HTML: autoescape turns an apostrophe into &#39;.
    return str(escape(rules.get_rule(rule_id).snippet_text))


def missing_blocks(html):
    return re.findall(r'<div class="flag-missing">(.*?)</div>', html, re.S)


def test_item_3_r3_is_a_missing_card_with_its_snippet_and_no_mark(client):
    r = client.get("/review/3")
    blocks = missing_blocks(r.text)
    assert len(blocks) == 1
    assert "Missing: add this" in blocks[0] and snippet_of("R3") in blocks[0]
    assert "Nothing to highlight in the copy" in blocks[0]
    assert len(pane(r.text).marks) == 1                      # only R4's phrase is highlighted


def test_item_14_has_three_missing_cards_and_no_marks(client):
    r = client.get("/review/14")
    blocks = missing_blocks(r.text)
    assert len(blocks) == 3
    for rid in ("R2", "R3", "R7"):
        assert any(snippet_of(rid) in b for b in blocks)
    assert pane(r.text).marks == []
    assert 'href="#flag-' not in r.text                      # no dead anchors


@pytest.mark.parametrize("sid", range(1, 15))
def test_missing_cards_never_link_and_phrase_cards_never_say_missing(client, sid):
    html = client.get(f"/review/{sid}").text
    cards = re.findall(r'<li class="flag-card">(.*?)\n    </li>', html, re.S)
    for c in cards:
        if "Missing: add this" in c:
            assert "href=" not in c and "flag-occurrences" not in c
        else:
            assert "flag-snippet" not in c


def test_mixed_phrase_and_missing_cards_in_severity_order(client):
    html = client.get("/review/1").text                       # R1 phrase, R2 and R5 missing
    heads = re.findall(r"<h3>.*?(R\d):", html)
    assert heads == ["R1", "R2", "R5"]
    assert len(missing_blocks(html)) == 2


def test_item_5_v1_missing_cards_only(client):
    html = client.get("/review/5?v=1").text
    assert len(missing_blocks(html)) == 2
    assert snippet_of("R2") in html and snippet_of("R5") in html


def test_snippet_is_escaped(client, monkeypatch):
    real = rules.get_rule

    def patched(rule_id):
        rule = real(rule_id)
        if rule_id == "R3":
            rule = dataclasses.replace(rule, snippet_text="<script>alert(1)</script> & <b>x</b>")
        return rule
    monkeypatch.setattr(rules, "get_rule", patched)
    html = client.get("/review/3").text
    assert "<script>alert" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;x&lt;/b&gt;" in html


def test_removed_rule_offers_no_snippet(client):
    with db.connect() as c:
        c.execute("UPDATE flag SET rule_id = 'R99' WHERE rule_id = 'R3'")
    html = client.get("/review/3").text
    assert "Rule no longer available" in html
    block = missing_blocks(html)[0]
    assert "flag-snippet" not in block                        # nothing offered for an unknown rule


def test_dismissed_missing_rule_has_no_card(client):
    with db.connect() as c:
        vid = c.execute("SELECT id FROM version WHERE submission_id = 3").fetchone()[0]
        c.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                  " VALUES (?, 'R3', 'n', 'x', '2026-10-01T00:00:00Z')", (vid,))
    assert missing_blocks(client.get("/review/3").text) == []
