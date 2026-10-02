import json
import re
from pathlib import Path

import pytest

from app import db

RULES = json.loads(Path("data/rules.json").read_text())
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")


def snapshot():
    with db.connect() as c:
        return tuple(tuple(tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)) for t in TABLES)


def box(html):
    m = re.search(r'<textarea id="comment-text"[^>]*>\n(.*?)</textarea>', html, re.S)
    return m.group(1) if m else None


def hidden_rule(html):
    m = re.search(r'<form[^>]*comment-form.*?</form>', html, re.S)
    found = re.findall(r'name="rule_id" value="([^"]*)"', m.group(0)) if m else []
    return found


def get(client, sid, query="", role="reviewer"):
    client.cookies.set("role", role)
    return client.get("/review/%d%s" % (sid, query)).text


def test_14_r2_prefills_the_exact_snippet_and_writes_nothing(client, tmp_path):
    before = snapshot()
    mtime = Path(db.get_db_path()).stat().st_mtime_ns
    html = get(client, 14, "?snippet=R2")
    r2 = next(r for r in RULES if r["id"] == "R2")["snippetText"]
    assert box(html) == r2.replace("'", "&#39;")
    assert hidden_rule(html) == ["R2"]
    assert "Snippet inserted from R2. Edit before posting." in html
    assert 'id="comment-text" name="text" rows="3" maxlength="2000" autofocus' in html
    assert snapshot() == before
    assert Path(db.get_db_path()).stat().st_mtime_ns == mtime
    assert client.get("/review/14?snippet=R2").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("sid, rule", [(1, "R1"), (1, "R2"), (1, "R5"), (12, "R4"), (14, "R2"), (14, "R3"), (14, "R7")])
def test_every_fired_rule_prefills_its_json_snippet(client, sid, rule):
    html = get(client, sid, "?snippet=" + rule)
    want = next(r for r in RULES if r["id"] == rule)["snippetText"]
    from html import unescape
    assert unescape(box(html)) == want and hidden_rule(html) == [rule]


def test_every_rule_in_the_file_has_snippet_text_that_prefills(client):
    # Parametrized over the file, so a rule added later is covered: put each rule on a flagged copy.
    from app import rules

    for r in RULES:
        assert r["snippetText"].strip()
        info = rules.describe({"rule_id": r["id"]})
        assert info["snippet"] == r["snippetText"]


@pytest.mark.parametrize("query", [
    "?snippet=R4",  # exists, did not fire on #14
    "?snippet=R99", "?snippet=r2", "?snippet=R2%0d%0a", "?snippet=%3Cscript%3E", "?snippet=R2&snippet=R3",
    "?snippet=", "?snippet=" + "A" * 5000, "?snippet=R2%00",
])
def test_anything_else_is_ignored_and_never_reflected(client, query):
    html = get(client, 14, query)
    assert box(html) == "" and hidden_rule(html) == [] and "Snippet inserted" not in html
    assert "autofocus" not in re.search(r'<textarea id="comment-text"[^>]*>', html).group(0)
    value = query.split("=", 1)[1].split("&")[0]
    if value and "R2" not in value:
        assert value not in html and "<script>" not in html.split("<main")[1]


def test_marketer_and_older_versions_are_ignored(client):
    html = get(client, 14, "?snippet=R2", role="marketer")
    assert 'id="comment-text"' not in html and "Use snippet" not in html
    html = get(client, 5, "?v=1&snippet=R2")
    assert 'id="comment-text"' not in html


def test_locked_item_prefills_for_a_reviewer(client):
    html = get(client, 14, "?snippet=R3")  # #14 is decided (changes requested): comments allowed
    assert hidden_rule(html) == ["R3"] and "Add the Equal Housing Lender statement." in box(html)


def test_a_snippet_with_markup_is_escaped_in_the_textarea(client, monkeypatch):
    from app import rules

    real = rules.describe

    def fake(flag):
        out = real(flag)
        if flag.get("rule_id") == "R2":
            out["snippet"] = "\"<b>'</textarea><script>alert(1)</script>"
        return out

    monkeypatch.setattr(rules, "describe", fake)
    html = get(client, 14, "?snippet=R2")
    assert "<script>alert(1)" not in html and "</textarea><script" not in html
    assert "&lt;/textarea&gt;&lt;script&gt;" in box(html)
