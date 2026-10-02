"""Phase 7 steps 25 and 26: the first impression, and the marketer's empty and edge views."""
import re
import threading
from datetime import timedelta
from html import unescape

import pytest
from fastapi.testclient import TestClient

from app import clock, db
from app.main import app
from app.routes import pages
from tests.test_phase7_edge_matrix import as_role, counts, stored_copy
from tests.test_queue_page import body_rows, row_for

HEADERS = {"origin": "http://testserver"}


def reset(client):
    pages.reset_cooldown.clear()
    assert client.post("/reset", data={"confirm": "reset"}, headers=HEADERS, follow_redirects=False).status_code == 303


def ids(html):
    return [int(i) for i in re.findall(r'<a href="/review/(\d+)', html)]


# ---- step 25: first impression ----

@pytest.mark.weekday("wednesday")
def test_a_fresh_start_opens_into_a_populated_queue_with_the_urgent_items_first(client):
    html = client.get("/").text
    rows = body_rows(html)
    assert len(rows) == 14
    assert ids(html)[:3] == [11, 1, 2]                                    # overdue first, then the two rush items
    for sid, word in ((11, "Overdue by 1 day"), (1, "Rush: launches tomorrow"), (2, "Rush: launches in 2 business days")):
        assert word in re.sub(r"<[^>]+>", " ", re.sub(r"\s+", " ", row_for(html, sid)))


def test_the_summary_says_how_many_need_attention(client):
    assert re.search(r"14 items \u00b7 4 need attention", client.get("/").text)


def test_reset_gives_the_same_first_impression(client):
    client.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS)
    before = client.get("/").text
    reset(client)
    after = client.get("/").text
    assert ids(after)[:3] == [11, 1, 2] and "Approved" not in row_for(after, 3)
    assert len(body_rows(after)) == 14 and "Demo reset to the original data" not in after


def test_the_most_urgent_item_with_flags_is_one_click_away(client):
    html = client.get("/").text
    first_flagged = next(i for i in ids(html) if not re.search(r"No flags", re.sub(r"\s+", " ", row_for(html, i))))
    assert first_flagged in (11, 1)                                       # within the first two rows
    page = client.get(re.search(r'href="(/review/%d(?:\?[^"]*)?)"' % first_flagged, html).group(1)).text
    assert 'class="flag-card' in page and 'action="/review/%d/decision"' % first_flagged in page


def test_each_urgent_row_links_straight_to_its_review(client):
    html = client.get("/").text
    for sid in (11, 1, 2):
        assert client.get("/review/%d" % sid).status_code == 200 and 'href="/review/%d' % sid in row_for(html, sid)


def test_the_queue_needs_no_javascript_and_no_second_request(client):
    html = client.get("/").text
    assert len(body_rows(html)) == 14
    assert not re.search(r"hx-(get|post|trigger)|fetch\(|XMLHttpRequest", html)
    assert re.findall(r'<script[^>]*src="([^"]+)"', html) == [re.search(r'src="(/static/theme[^"]+)"', html).group(1),      # the theme choice, applied before paint
                                                              re.search(r'src="(/static/htmx[^"]+)"', html).group(1),
                                                              re.search(r'src="(/static/queue[^"]+)"', html).group(1)]
    assert 'class="queue"' in html and "<noscript" not in html            # nothing is hidden until a script runs


def test_a_restart_on_an_empty_file_seeds_once_and_a_restart_on_a_used_file_keeps_the_data(db_path):
    with TestClient(app) as c:
        assert len(body_rows(c.get("/").text)) == 14
        c.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS)
    with TestClient(app) as c:                                            # the host restarted the app
        html = c.get("/").text
        assert len(body_rows(html)) == 14 and "Approved" in row_for(html, 3)
        assert counts()["decision"] == 8


def test_two_simultaneous_first_starts_do_not_double_seed(db_path):
    errors = []

    def start():
        try:
            with TestClient(app) as c:
                c.get("/")
        except Exception as exc:                                          # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and counts()["submission"] == 14 and counts()["version"] == 16


def test_the_first_screen_explains_the_demo_without_a_tour(client):
    html = client.get("/").text
    assert "Resets data for everyone using this demo." in html and "Flags are assist-only" in html
    assert html.count("<h1") == 1 and "Review queue" in html


def test_the_whole_loop_from_the_front_door(client):
    """The unaided path a first-time person takes: submit, review, request changes, resubmit, approve."""
    as_role(client, "marketer", "Maya Chen")
    first = "Guaranteed approval. Rates as low as 5.99%."
    launch = (clock.today() + timedelta(days=30)).isoformat()
    r = client.post("/submit", data={"title": "Front door", "product": "loan", "channel": "email", "copy": first,
                                     "notes": "", "launch_date": launch}, headers=HEADERS, follow_redirects=True)
    assert r.status_code == 200 and "Front door" in r.text and "is in the review queue" in r.text
    sid = int(re.search(r'href="/review/(\d+)">Open it', r.text).group(1))
    as_role(client, "reviewer")
    assert 'href="/review/%d' % sid in client.get("/").text
    page = client.get("/review/%d" % sid).text
    assert "Guaranteed approval" in page and re.search(r'<li class="flag-card"', page)
    assert client.post("/review/%d/decision" % sid, data={"outcome": "changes_requested", "version": "1",
                       "reason": "Remove the guarantee and add the APR."}, headers=HEADERS).status_code in (200, 303)
    as_role(client, "marketer", "Maya Chen")
    mine = client.get("/mine").text
    assert "Needs your action" in mine and "Remove the guarantee and add the APR." in mine and "Edit and resubmit" in mine
    fixed = "Apply today. Rates from 5.99% APR. Subject to credit approval and Equal Housing Lender."
    r = client.post("/resubmit/%d" % sid, data={"copy": fixed, "notes": "Fixed.", "launch_date": launch, "base_version": "1"},
                    headers=HEADERS, follow_redirects=True)
    assert r.status_code == 200 and "Resubmitted as v2" in r.text
    as_role(client, "reviewer")
    page = client.get("/review/%d?diff=1" % sid).text
    assert "removed: " in page and "added: " in page
    assert client.post("/review/%d/decision" % sid, data={"outcome": "approved", "version": "2"}, headers=HEADERS,
                       follow_redirects=False).status_code == 303
    final = client.get("/review/%d" % sid).text
    assert "Locked: Approved" in final and 'action="/review/%d/decision"' % sid not in final
    trail = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", final[final.index("trail-heading"):]))
    order = [trail.find(x) for x in ("v1 submitted", "Changes requested", "v2 submitted", "Approved")]
    assert -1 not in order and order == sorted(order)


# ---- step 26: the marketer's empty and edge views ----

@pytest.mark.parametrize("fresh", [True, False])
def test_sam_patel_sees_a_calm_empty_page_with_a_way_to_submit(client, fresh):
    if not fresh:
        as_role(client, "marketer", "Maya Chen")
        client.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS)
        reset(client)
    html = as_role(client, "marketer", "Sam Patel").get("/mine").text
    assert "You haven't submitted anything yet." in html and 'href="/submit"' in html
    assert html.count('href="/submit"') >= 2 and "<table" not in html and "mine-group" not in html
    assert "Needs your action" not in html


@pytest.mark.parametrize("who,first", [("Maya Chen", None), ("Jordan Lee", "Needs your action")])
def test_the_order_of_groups_is_action_first_for_those_who_have_action_items(client, who, first):
    html = as_role(client, "marketer", who).get("/mine").text
    headings = re.findall(r'<h2 id="group-\w+">([^<(]+)', html)
    assert headings[0].strip() == (first or "In progress")
    if first:
        assert headings.index("Needs your action ") < headings.index("In progress ")


@pytest.mark.parametrize("param", ["marketer", "name", "who", "user", "as", "submitted_by"])
def test_a_query_parameter_cannot_pick_whose_submissions_are_shown(client, param):
    as_role(client, "marketer", "Sam Patel")
    for value in ("Maya Chen", "Jordan Lee", "maya", "' OR 1=1 --"):
        html = client.get("/mine?%s=%s" % (param, value)).text
        assert "Needs your action" not in html and "You haven't submitted anything yet." in html
        assert "Personal loan holiday email" not in html and "Home equity display ad" not in html


def test_the_submitted_banner_only_shows_for_the_owner(client):
    as_role(client, "marketer", "Sam Patel")
    assert "is in the review queue" not in client.get("/mine?submitted=1").text          # #1 is Maya's
    as_role(client, "marketer", "Maya Chen")
    assert "is in the review queue" in client.get("/mine?submitted=1").text


def test_changes_requested_shows_the_full_reason_and_a_resubmit_link(client):
    html = as_role(client, "marketer", "Jordan Lee").get("/mine").text
    reason = "Three required disclosures are missing: the APR, the Equal Housing Lender statement, and a pointer to full terms."
    assert reason in unescape(html) and 'href="/resubmit/14"' in html and "Edit and resubmit" in html


def test_a_reason_with_markup_is_shown_as_text(client):
    with db.connect() as c:
        from tests.helpers import tamper
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 14", ('<script>alert(1)</script> & "quotes"',))
    html = as_role(client, "marketer", "Jordan Lee").get("/mine").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_rejected_item_shows_its_reason_the_history_and_a_fix_link(client):
    mine = as_role(client, "marketer", "Jordan Lee").get("/mine").text
    assert "Rejected" in mine and 'href="/resubmit/8"' in mine and "Fix and resubmit as new version" in mine
    page = client.get("/review/8").text
    assert "The copy promises approval to everyone" in page and "Audit trail" in page and "v1 submitted" in re.sub(r"<[^>]+>", " ", page)


@pytest.mark.parametrize("sid", [1, 3, 5, 6, 8, 12, 14])
def test_a_marketer_opening_anyones_item_gets_a_read_only_view(client, sid):
    html = as_role(client, "marketer", "Sam Patel").get("/review/%d" % sid).text
    assert "Only reviewers can decide." in html or "Locked" in html or "decision-readonly" in html
    for action in ("decision", "dismiss", "comment"):
        assert 'action="/review/%d/%s"' % (sid, action) not in html, (sid, action)
    assert 'href="/mine"' in html                                                        # the way back is their own list


def test_a_marketer_can_resubmit_only_their_own_items(client):
    before = counts()
    r = as_role(client, "marketer", "Sam Patel").get("/resubmit/14")
    assert r.status_code == 200 and 'action="/resubmit/14"' not in r.text and "belongs to Jordan Lee" in r.text
    assert counts() == before
