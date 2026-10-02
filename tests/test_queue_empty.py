import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from tests.test_queue_filters import ids, selected, summary

NO_MATCH = "No items match these filters."
NO_SUBS = "No submissions yet."


def empty_state(html):
    m = re.search(r'<p class="empty-state" role="status">(.*?)</p>', html, re.S)
    return m.group(1) if m else None


@pytest.fixture
def empty_client(db_path):
    """The app running over a database with the schema but no submissions (not seedable in the app)."""
    from app.main import app

    db.init_schema()
    return TestClient(app)


# ---- zero filter results ----

def test_zero_results_shows_the_message_and_clear_link(client):
    html = client.get("/?status=rejected&product=card").text
    assert client.get("/?status=rejected&product=card").status_code == 200
    text = empty_state(html)
    assert NO_MATCH in text
    assert '<a href="/">Clear filters</a>' in text


def test_zero_results_has_no_table_at_all(client):
    html = client.get("/?status=rejected&product=card").text
    assert "<table" not in html and "<tbody" not in html
    assert ids(html) == []


def test_zero_results_keeps_the_filter_form_with_the_choices(client):
    html = client.get("/?status=rejected&product=card").text
    assert selected(html, "status") == ["rejected"] and selected(html, "product") == ["card"]
    assert selected(html, "channel") == [""]
    assert 'class="filters"' in html


def test_zero_results_summary_is_exactly_0_items(client):
    assert summary(client.get("/?status=rejected&product=card").text) == "0 items"


def test_the_clear_link_leads_back_to_all_14_rows(client):
    html = client.get("/?status=rejected&product=card").text
    href = re.search(r'<a href="([^"]*)">Clear filters</a>', empty_state(html) + '<a href="/">Clear filters</a>').group(1)
    assert href == "/"
    assert len(ids(client.get(href).text)) == 14


def test_state_is_announced_to_screen_readers(client):
    assert 'class="empty-state" role="status"' in client.get("/?status=rejected&product=card").text


def test_filter_message_is_not_shown_when_there_are_results(client):
    for url in ("/", "/?status=new", "/?product=mortgage"):
        html = client.get(url).text
        assert NO_MATCH not in html and NO_SUBS not in html and empty_state(html) is None
        assert html.count("<table") == 1


@pytest.mark.parametrize("query", ["?status=rejected&product=card", "?status=approved&channel=display",
                                   "?status=changes_requested&product=loan"])
def test_every_impossible_combination_shows_the_message(client, query):
    assert NO_MATCH in client.get("/" + query).text


def test_hostile_filter_values_do_not_cause_the_empty_state_or_reflect(client):
    html = client.get("/?status=%3Cscript%3Ealert(1)%3C/script%3E").text
    assert empty_state(html) is None and len(ids(html)) == 14 and "alert(1)" not in html


def test_zero_results_banner_and_role(client):
    client.cookies.set("role", "marketer")
    html = client.get("/?status=rejected&product=card&reset=done").text
    assert NO_MATCH in html and "Demo reset to the original data." in html


# ---- a queue with nothing in it ----

def test_empty_database_shows_no_submissions_and_a_submit_link(empty_client):
    r = empty_client.get("/")
    assert r.status_code == 200
    text = empty_state(r.text)
    assert NO_SUBS in text
    assert '<a href="/submit">Submit the first one</a>' in text
    assert NO_MATCH not in r.text
    assert "<table" not in r.text


def test_empty_database_summary_is_exactly_0_items_without_attention_noise(empty_client):
    text = summary(empty_client.get("/").text)
    assert text == "0 items"
    assert "attention" not in text


def test_empty_database_with_filters_does_not_blame_the_filters(empty_client):
    html = empty_client.get("/?status=new&product=loan").text
    assert NO_SUBS in html and NO_MATCH not in html


def test_empty_database_state_is_announced(empty_client):
    assert 'class="empty-state" role="status"' in empty_client.get("/").text


def test_emptying_a_seeded_app_switches_the_message(client, db_path):
    assert len(ids(client.get("/").text)) == 14
    c = sqlite3.connect(str(db_path))
    c.execute("DELETE FROM submission")   # cascades to everything else
    c.commit()
    c.close()
    assert NO_SUBS in client.get("/").text
    assert NO_SUBS in client.get("/?status=new").text


def test_a_single_submission_is_a_normal_table_not_an_empty_state(client, db_path):
    c = sqlite3.connect(str(db_path))
    c.execute("DELETE FROM submission WHERE id != 10")
    c.commit()
    c.close()
    html = client.get("/").text
    assert empty_state(html) is None and ids(html) == [10]
    assert summary(html) == "1 item · none need attention"
    assert NO_MATCH in client.get("/?status=rejected").text   # one item, but not a rejected one


def test_reset_from_an_empty_database_brings_the_queue_back(empty_client):
    assert NO_SUBS in empty_client.get("/").text
    r = empty_client.post("/reset", data={"confirm": "reset"})
    assert r.status_code == 200 and len(ids(r.text)) == 14


# ---- markup ----

def test_empty_states_have_no_inline_styles_or_scripts(client, empty_client):
    for html in (client.get("/?status=rejected&product=card").text, empty_client.get("/").text):
        assert not re.search(r"\sstyle\s*=", html) and not re.search(r"<script(?![^>]*\bsrc=)", html)


def test_empty_pages_keep_headers_and_no_store(empty_client):
    r = empty_client.get("/")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"


def test_empty_kind_unit(conn):
    from app.queue import empty_kind
    assert empty_kind(conn, [{"x": 1}], {"status": "new"}) is None
    assert empty_kind(conn, [], {"status": None, "product": None, "channel": None}) == "empty"
    assert empty_kind(conn, [], {"status": "new", "product": None, "channel": None}) == "empty"   # nothing exists
    conn.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by,"
                 " created_at, current_version) VALUES (1, 't', 'loan', 'email', 'new', '2030-01-01', 'm', 'x', 1)")
    assert empty_kind(conn, [], {"status": "rejected", "product": None, "channel": None}) == "filtered"
    assert empty_kind(conn, [], {"status": None, "product": None, "channel": None}) == "empty"
