import re
from html import unescape

import pytest

from app import db
from tests.test_review_decision_route import post


def form(html):
    m = re.search(r'<form method="post" action="[^"]*" class="decision-form">.*?</form>', html, re.S)
    return m.group(0) if m else None


def reviewer(client):
    client.cookies.set("role", "reviewer")


def test_item_3_as_reviewer_has_the_three_buttons_label_and_hidden_version(client):
    reviewer(client)
    f = form(client.get("/review/3").text)
    assert f
    assert re.findall(r'<button type="submit" name="outcome" value="(\w+)"[^>]*>([^<]+)</button>', f) == [
        ("approved", "Approve"), ("changes_requested", "Request changes"), ("rejected", "Reject")]
    assert '<input type="hidden" name="version" value="1">' in f
    assert re.search(r'<label for="decision-reason">Reason \(required for Request changes and Reject\)</label>', f)
    assert re.search(r'<textarea id="decision-reason" name="reason"[^>]*maxlength="2000"', f)
    assert 'action="/review/3/decision"' in f and 'method="post"' in f
    assert f.count("<textarea") == 1


def test_approve_is_first_and_distinct(client):
    f = form(client.get("/review/3").text)
    assert f.index("decision-approve") < f.index("decision-changes") < f.index("decision-reject")


def test_marketer_sees_no_form_only_a_note(client):
    client.cookies.set("role", "marketer")
    html = client.get("/review/3").text
    assert form(html) is None and "<textarea" not in html and 'name="outcome"' not in html
    assert "Only reviewers can decide." in html


@pytest.mark.parametrize("sid", [6, 8, 13, 14])
def test_locked_items_have_no_form_and_no_buttons(client, sid):
    html = client.get(f"/review/{sid}").text
    assert 'name="outcome"' not in html and "<textarea" not in html and "Decision</h2>" not in html
    assert "Only reviewers can decide" not in html
    assert re.search(r"Locked|Changes requested by", html)               # something explains why


def test_item_7_current_is_locked_and_old_version_has_no_form(client):
    assert 'name="outcome"' not in client.get("/review/7").text
    assert 'name="outcome"' not in client.get("/review/7?v=1").text


def test_item_5_current_has_the_form_and_old_version_does_not(client):
    cur = form(client.get("/review/5").text)
    assert cur and 'name="version" value="2"' in cur
    assert 'name="outcome"' not in client.get("/review/5?v=1").text


@pytest.mark.parametrize("sid", [1, 2, 3, 4, 9, 10, 11, 12])
def test_every_open_item_offers_the_form(client, sid):
    assert form(client.get(f"/review/{sid}").text)


def test_form_disappears_after_deciding(client):
    assert form(client.get("/review/3").text)
    post(client)
    assert form(client.get("/review/3").text) is None


def test_status_locked_without_decision_row_offers_no_form(client):
    with db.connect() as c:
        c.execute("UPDATE submission SET status = 'approved' WHERE id = 3")
    assert form(client.get("/review/3").text) is None


def test_no_inline_handlers_or_styles_in_the_form(client):
    f = form(client.get("/review/3").text)
    assert not re.search(r"\son\w+=|\sstyle=|<script", f)


def test_failed_validation_keeps_the_reason_escaped(client):
    evil = "<script>alert(1)</script> & \"q\" 'x' " + "y" * 2001
    r = post(client, outcome="rejected", reason=evil)                    # too long, so refused
    assert r.status_code == 422
    f = form(r.text)
    assert "<script>alert" not in r.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in f
    m = re.search(r'<textarea[^>]*>\n(.*?)</textarea>', f, re.S)
    assert unescape(m.group(1)) == evil                                  # exact, no stray whitespace


def test_blank_reason_error_keeps_other_state(client):
    r = post(client, outcome="changes_requested", reason="   ")
    assert r.status_code == 422
    f = form(r.text)
    assert 'name="version" value="1"' in f and "A reason is required" in r.text


def test_reason_with_leading_newline_survives_the_round_trip(client):
    reason = "\n\nstart" + "z" * 2001
    r = post(client, outcome="rejected", reason=reason)
    m = re.search(r'<textarea[^>]*>\n(.*?)</textarea>', form(r.text), re.S)
    assert unescape(m.group(1)) == reason


def test_conflict_page_does_not_offer_a_form_for_a_decided_version(client):
    post(client)
    r = post(client)
    assert r.status_code == 409 and form(r.text) is None


def test_forms_only_post_to_their_own_submission(client):
    for sid in (1, 2, 3, 4):
        assert f'action="/review/{sid}/decision"' in client.get(f"/review/{sid}").text


def test_approve_and_reject_styles_are_not_overridden_by_the_generic_button_rule():
    # Regression: ".decision-buttons button" (class + element) beat ".decision-approve" (one class),
    # leaving white text on a white background.
    css = open("app/static/style.css").read()
    assert re.search(r"\.decision-buttons \.decision-approve \{[^}]*background: var\(--accent\)[^}]*color: var\(--accent-text\)", css)
    assert re.search(r"\.decision-buttons \.decision-reject \{[^}]*border: 2px solid", css)
    assert not re.search(r"^\.decision-approve \{", css, re.M)
