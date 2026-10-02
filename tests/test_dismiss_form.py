import re
from pathlib import Path

import pytest

from app import review


def forms(html):
    return re.findall(r'<form method="post" action="/review/\d+/dismiss".*?</form>', html, re.S)


def get(client, sid, role="reviewer", query=""):
    client.cookies.set("role", role)
    return client.get("/review/%d%s" % (sid, query)).text


def test_12_has_one_complete_dismiss_form(client):
    html = get(client, 12)
    (form,) = forms(html)
    assert 'action="/review/12/dismiss"' in form and 'method="post"' in form
    assert '<input type="hidden" name="rule_id" value="R4">' in form
    assert '<input type="hidden" name="version" value="1">' in form
    assert '<label for="dismiss-note-R4">Note (required)</label>' in form
    assert re.search(r'<textarea id="dismiss-note-R4" name="note"[^>]*maxlength="1000"', form)
    assert "It can&#39;t be undone." in form or "It can't be undone." in form
    assert '<button type="submit">Dismiss flag</button>' in form
    assert '<details class="flag-dismiss">' in html  # closed until needed


@pytest.mark.parametrize("sid", [14, 6, 7, 8, 13])
def test_decided_items_have_no_dismiss_form_or_button(client, sid):
    html = get(client, sid)
    assert not forms(html) and "Dismiss flag" not in html and 'class="flag-dismiss"' not in html


def test_dismissed_flag_has_no_form(client):
    html = get(client, 13)
    assert "Dismissed (1)" in html and "R1:" in html and not forms(html)


def test_older_versions_have_no_forms(client):
    assert not forms(get(client, 5, query="?v=1"))


def test_marketer_sees_no_forms(client):
    html = get(client, 12, role="marketer")
    assert not forms(html) and "Dismiss flag" not in html


def test_clean_item_has_nothing_to_dismiss(client):
    html = get(client, 5)  # v2 fires nothing and is still in review
    assert "No flags detected" in html and not forms(html)


def test_several_flags_get_independent_forms_with_unique_ids(client):
    html = get(client, 1)
    fs = forms(html)
    assert len(fs) >= 3
    rules = [re.search(r'name="rule_id" value="(R\d)"', f).group(1) for f in fs]
    assert len(set(rules)) == len(rules)
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate DOM ids"
    for r in rules:
        assert 'for="dismiss-note-%s"' % r in html and 'id="dismiss-note-%s"' % r in html


def test_no_inline_handlers_or_styles(client):
    for f in forms(get(client, 1)):
        assert not re.search(r'\son\w+=|style=', f)
    template = (Path(review.__file__).parent / "templates" / "_flag_cards.html").read_text()
    assert not re.search(r'\sonclick=|\sstyle=', template)


def test_failed_post_reopens_the_right_details_with_the_note_kept(client):
    client.cookies.set("role", "reviewer")
    html = client.post("/review/1/dismiss", data={"rule_id": "R2", "version": "1", "note": "x" * 1001}).text
    opened = re.findall(r'<details class="flag-dismiss" open>(.*?)</details>', html, re.S)
    assert len(opened) == 1 and 'value="R2"' in opened[0] and "x" * 1001 in opened[0]
    closed = re.findall(r'<details class="flag-dismiss">.*?</details>', html, re.S)
    assert len(closed) >= 2 and all("x" * 1001 not in c for c in closed)
    assert 'role="alert" tabindex="-1" autofocus' in opened[0]


def test_hostile_rule_id_is_never_reflected(client):
    client.cookies.set("role", "reviewer")
    r = client.post("/review/1/dismiss", data={"rule_id": "<script>alert(1)</script>", "version": "1", "note": ""})
    assert "alert(1)" not in r.text
    assert 'class="flag-dismiss" open' not in r.text  # no card matches, so none is opened


def test_precheck_cards_never_get_forms(client):
    client.cookies.set("role", "marketer")
    r = client.post("/submit/check", data={"product": "loan", "channel": "email",
                                           "copy": "Guaranteed approval for everyone"})
    assert r.status_code == 200 and not forms(r.text) and "Dismiss flag" not in r.text
