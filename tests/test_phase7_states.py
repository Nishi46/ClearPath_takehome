"""Phase 7 steps 3 to 5: no flags, empty filter results, empty queue."""
import re
from datetime import timedelta

import pytest

from app import clock, db
from tests.test_phase7_edge_matrix import as_role, counts, row_text, stored_copy
from tests.test_queue_page import body_rows, row_for

NO_FLAGS = "No flags detected."
ALL_DISMISSED = "No open flags. Every flag on this version was dismissed."
HEADERS = {"origin": "http://testserver"}


def visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---- step 3: no flags found ----

@pytest.mark.parametrize("sid", [6, 10])
def test_clean_items_say_it_once_and_have_no_cards_or_highlights(client, sid):
    html = client.get(f"/review/{sid}").text
    assert html.count(NO_FLAGS) == 1 and ALL_DISMISSED not in html
    assert '<mark id="flag-' not in html and 'class="flag-mark' not in html
    assert '<li class="flag-card"' not in html


def test_all_dismissed_never_shows_the_clean_copy(client):
    html = client.get("/review/13").text
    assert ALL_DISMISSED in html and NO_FLAGS not in html


@pytest.mark.parametrize("sid", [6, 10])
def test_zero_flags_never_show_the_dismissed_copy(client, sid):
    assert ALL_DISMISSED not in client.get(f"/review/{sid}").text


@pytest.mark.parametrize("sid", [6, 10])
def test_clean_item_keeps_the_notes_and_the_not_a_guarantee_line(client, sid):
    html = visible(client.get(f"/review/{sid}").text)
    assert "Flags assist the reviewer. They never decide." in html
    assert "Rules are illustrative, not legal advice." in html


def test_clean_open_item_still_has_the_decision_buttons(client):
    html = client.get("/review/10").text
    assert 'action="/review/10/decision"' in html and html.count('name="outcome"') >= 3


@pytest.mark.parametrize("sid", [6, 10])
def test_queue_zero_flags_has_words_not_just_a_digit(client, sid):
    cell = re.search(r'data-label="Flags".*?</td>', row_for(client.get("/").text, sid), re.S).group(0)
    assert "No flags" in cell and "visually-hidden" in cell


def test_precheck_says_no_flags_and_not_a_guarantee(client):
    as_role(client, "marketer", "Maya Chen")
    r = client.post("/submit/check", headers=HEADERS,
                    data={"product": "mortgage", "channel": "email", "copy": stored_copy(6)})
    assert r.status_code == 200
    assert NO_FLAGS in r.text and "not a guarantee of compliance" in r.text


def test_clean_copy_containing_html_is_escaped_on_review_and_precheck(client):
    as_role(client, "marketer", "Maya Chen")
    evil = stored_copy(6) + "\n<script>alert(1)</script><img src=x onerror=alert(2)>"
    r = client.post("/submit/check", headers=HEADERS, data={"product": "mortgage", "channel": "email", "copy": evil})
    assert "<script>alert(1)" not in r.text and "<img src=x" not in r.text
    r = client.post("/submit", follow_redirects=False, headers=HEADERS, data={
        "title": "Escaped", "product": "mortgage", "channel": "email", "copy": evil, "notes": "",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
    assert r.status_code == 303
    sid = int(r.headers["location"].split("=")[1])
    page = as_role(client, "reviewer").get(f"/review/{sid}").text
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# ---- step 4: empty filter results ----

def test_rejected_and_card_is_empty_with_a_summary_that_reads_right(client):
    html = client.get("/?status=rejected&product=card").text
    assert "No items match these filters." in html and "<table" not in html
    assert re.search(r'class="summary">0 items', html)


def test_filters_still_show_the_selection_after_a_zero_result(client):
    html = client.get("/?status=rejected&product=card").text
    assert '<option value="rejected" selected>' in html and '<option value="card" selected>' in html


def test_clear_filters_link_returns_the_whole_queue(client):
    html = client.get("/?status=rejected&product=card").text
    assert html.count('href="/">Clear filters') == 2          # the form link and the empty-state link
    assert len(body_rows(client.get("/").text)) == 14


@pytest.mark.parametrize("query", [
    "status=bogus", "status=", "status=new&status=rejected", "status=" + "a" * 5000, "status=%00",
    "status=%E2%80%AE", "product=%27+OR+1%3D1+--", "channel=%3Cscript%3Ealert(1)%3C%2Fscript%3E",
    "status=new%0d%0aSet-Cookie:x=1", "x=1&y=2", "status[]=new", "product=Card", "product=card%20",
])
def test_hostile_filter_values_never_error_or_echo(client, query):
    r = client.get("/?" + query)
    assert r.status_code == 200
    assert "<script>alert(1)" not in r.text and "Set-Cookie" not in r.headers.get("set-cookie", "")
    assert len(re.findall(r"<tr\b", r.text)) in (0, 15) or "No items match" in r.text


@pytest.mark.parametrize("bad", ["' OR 1=1 --", "x'; DROP TABLE submission; --", "new' UNION SELECT 1 --"])
def test_sql_in_a_filter_behaves_like_an_unknown_value(client, bad):
    from urllib.parse import quote

    before = counts()
    plain = client.get("/?status=bogus").text
    assert client.get("/?status=" + quote(bad)).text == plain
    assert counts() == before


def test_back_link_is_rebuilt_from_known_filters_only(client):
    html = client.get("/?status=in_review&evil=1&back=//evil.com").text
    links = re.findall(r'href="/review/\d+([^"]*)"', html)
    assert links and all("evil" not in link for link in links)


# ---- step 5: empty queue ----

@pytest.fixture
def empty(client):
    with db.connect() as c:
        c.execute("DELETE FROM submission")
    return client


@pytest.mark.parametrize("role", ["reviewer", "marketer"])
def test_empty_queue_copy_link_and_no_table(empty, role):
    html = as_role(empty, role).get("/").text
    assert "No submissions yet." in html and 'href="/submit"' in html
    assert "<table" not in html and "No items match" not in html
    assert re.search(r'class="summary">0 items', html)


def test_empty_queue_with_a_filter_still_says_empty_not_no_match(empty):
    # Nothing exists at all, so "no submissions yet" is the honest message (decision: empty wins).
    html = empty.get("/?status=new").text
    assert "No submissions yet." in html


def test_empty_queue_reading_writes_nothing(empty):
    before = counts()
    for _ in range(3):
        empty.get("/")
    assert counts() == before and before["submission"] == 0


def test_reset_from_empty_restores_the_14(empty):
    r = empty.post("/reset", data={"confirm": "reset"}, headers=HEADERS, follow_redirects=False)
    assert r.status_code in (200, 303)
    assert len(body_rows(empty.get("/").text)) == 14
