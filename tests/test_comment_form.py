import re
from pathlib import Path

import pytest

from app import review


def get(client, sid, role="reviewer", query=""):
    client.cookies.set("role", role)
    return client.get("/review/%d%s" % (sid, query)).text


def links(html):
    return re.findall(r'<a class="snippet-link" href="([^"]+)" data-snippet="[^"]*" data-rule="(R\d)">Use snippet</a>',
                      html)


def comment_form(html):
    m = re.search(r'<form method="post" action="/review/\d+/comment".*?</form>', html, re.S)
    return m.group(0) if m else None


def test_14_has_three_snippet_links_and_a_comment_form_although_decided(client):
    html = get(client, 14)
    assert [(h, r) for h, r in links(html)] == [
        ("/review/14?snippet=R2#comment-form", "R2"), ("/review/14?snippet=R3#comment-form", "R3"),
        ("/review/14?snippet=R7#comment-form", "R7")]
    form = comment_form(html)
    assert 'action="/review/14/comment"' in form and 'method="post"' in form and 'id="comment-form"' in form
    assert '<input type="hidden" name="version" value="1">' in form
    assert 'name="rule_id"' not in form  # a free comment has no link until a snippet is used
    assert '<label for="comment-text">Add a comment</label>' in form
    assert re.search(r'<textarea id="comment-text" name="text"[^>]*maxlength="2000"', form)
    assert '<button type="submit">Post comment</button>' in form
    assert 'id="snippet-note" class="snippet-note" role="status"' in form


def test_phrase_and_missing_cards_both_have_the_link(client):
    assert [r for _, r in links(get(client, 12))] == ["R4"]  # phrase rule
    assert "R2" in [r for _, r in links(get(client, 14))]    # missing-text rule


def test_dismissed_flags_keep_a_snippet_link(client):
    html = get(client, 13)  # R1 was dismissed; comments on a locked item are allowed
    assert [r for _, r in links(html)] == ["R1"]
    assert "dismissed by Alex Rivera" in html.split("Use snippet")[0].split("flag-dismissal")[-1]


def test_links_keep_the_back_filter_and_version(client):
    html = get(client, 14, query="?back=%2F%3Fstatus%3Dnew")
    assert all("back=%2F%3Fstatus%3Dnew" in h and "#comment-form" in h for h, _ in links(html))


def test_older_version_has_no_form_and_no_links_and_says_why(client):
    html = get(client, 5, query="?v=1")
    assert comment_form(html) is None and not links(html)
    assert "Comments can only be added to the current version." in html


def test_marketer_has_neither_and_is_told(client):
    html = get(client, 14, role="marketer")
    assert comment_form(html) is None and not links(html) and "Use snippet" not in html
    assert "Only reviewers can comment." in html


def test_unknown_rule_gets_no_link(client, monkeypatch):
    from app import rules

    real = rules.describe

    def fake(flag):
        out = real(flag)
        if flag.get("rule_id") == "R7":
            out["snippet"] = ""
        return out

    monkeypatch.setattr(rules, "describe", fake)
    assert [r for _, r in links(get(client, 14))] == ["R2", "R3"]


@pytest.mark.parametrize("sid", range(1, 15))
def test_controls_have_labels_and_ids_are_unique(client, sid):
    html = get(client, sid)
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    if comment_form(html):
        assert 'for="comment-text"' in html and 'id="comment-text"' in html


def test_no_inline_handlers_or_styles(client):
    form = comment_form(get(client, 14))
    assert not re.search(r'\son\w+=|style=', form)
    template = (Path(review.__file__).parent / "templates" / "review.html").read_text()
    assert not re.search(r'\sonclick=|\sstyle=', template)


def test_the_hostile_snippet_attribute_is_escaped(client, monkeypatch):
    from app import rules

    real = rules.describe

    def fake(flag):
        out = real(flag)
        if flag.get("rule_id") == "R2":
            out["snippet"] = '" onmouseover="alert(1)'
        return out

    monkeypatch.setattr(rules, "describe", fake)
    html = get(client, 14)
    assert 'data-snippet="&#34; onmouseover=&#34;alert(1)"' in html and 'onmouseover="alert' not in html


def test_after_a_failed_post_the_form_keeps_text_and_link(client):
    client.cookies.set("role", "reviewer")
    html = client.post("/review/14/comment", data={"text": "x" * 2001, "version": "1", "rule_id": "R3"}).text
    form = comment_form(html)
    assert 'name="rule_id" value="R3"' in form and "x" * 2001 in form and 'role="alert"' in form
