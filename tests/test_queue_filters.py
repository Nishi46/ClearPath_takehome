import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import clock, queue as queue_module
from app.queue import FILTER_OPTIONS, filters_from_query, list_queue, summary_text
from tests.test_queue_page import EXPECTED_ORDER, body_rows

from starlette.datastructures import QueryParams


def ids(html):
    return [int(i) for i in re.findall(r'<a href="/review/(\d+)(?:\?[^"]*)?">', html)]


def summary(html):
    return re.search(r'<p class="summary">(.*?)</p>', html).group(1)


def selected(html, name):
    select = re.search(r'<select id="filter-%s".*?</select>' % name, html, re.S).group(0)
    return re.findall(r'<option value="([^"]*)" selected>', select)


# ---- the form ----

def test_form_is_a_plain_get_form_with_labelled_selects(client):
    html = client.get("/").text
    form = re.search(r'<form class="filters".*?</form>', html, re.S).group(0)
    assert 'method="get"' in form and 'action="/"' in form
    for name, label in (("status", "Status"), ("product", "Product"), ("channel", "Channel")):
        assert f'<label for="filter-{name}">{label}</label>' in form
        assert f'<select id="filter-{name}" name="{name}">' in form
    assert 'type="submit"' in form and ">Apply<" in form
    assert '<a class="filter-apply filter-clear" href="/">Clear filters</a>' in form


def test_every_select_starts_with_all_then_the_real_options(client):
    html = client.get("/").text
    for name, options in FILTER_OPTIONS.items():
        select = re.search(r'<select id="filter-%s".*?</select>' % name, html, re.S).group(0)
        values = re.findall(r'<option value="([^"]*)"', select)
        assert values == [""] + [v for v, _ in options]
        assert "<option value=\"\" selected>All</option>" in select


def test_form_needs_no_javascript(client):
    form = re.search(r'<form class="filters".*?</form>', client.get("/").text, re.S).group(0)
    assert "<script" not in form and "hx-" not in form and not re.search(r"\son\w+=", form)


# ---- filtering ----

def test_status_in_review_returns_3_5_11_in_order(client):
    html = client.get("/?status=in_review").text
    assert ids(html) == [11, 3, 5]
    assert summary(html).startswith("3 items")
    assert selected(html, "status") == ["in_review"]
    assert selected(html, "product") == [""] and selected(html, "channel") == [""]


def test_selected_option_shows_its_label(client):
    html = client.get("/?status=in_review").text
    assert '<option value="in_review" selected>In review</option>' in html


def test_combined_filters_match_the_query(client, conn):
    html = client.get("/?status=new&product=loan&channel=email").text
    assert ids(html) == [r["id"] for r in list_queue(conn, "new", "loan", "email")]
    assert ids(html) == [1]
    assert selected(html, "status") == ["new"] and selected(html, "product") == ["loan"]
    assert selected(html, "channel") == ["email"]


def test_every_filter_option_works_through_the_page(client):
    for name, options in FILTER_OPTIONS.items():
        total = 0
        for value, _ in options:
            html = client.get(f"/?{name}={value}").text
            got = ids(html)
            assert got and len(got) == len(set(got))
            assert selected(html, name) == [value]
            total += len(got)
        assert total == 14   # each filter splits the 14 items with none lost or doubled


def test_sort_order_is_kept_under_a_filter(client):
    got = ids(client.get("/?product=mortgage").text)
    assert got == [i for i in EXPECTED_ORDER if i in got] and len(got) == 5


def test_filter_state_survives_a_reload_and_is_shareable(client):
    first = client.get("/?status=approved&product=mortgage").text
    second = client.get("/?status=approved&product=mortgage").text
    assert first == second
    assert ids(first) == [6]   # approved: #6 mortgage, #7 card, #13 loan
    fresh = client.__class__(client.app)   # a different client (no cookies) gets the same result
    assert ids(fresh.get("/?status=approved&product=mortgage").text) == [6]


def test_filters_work_for_both_roles_and_with_the_reset_banner(client):
    client.cookies.set("role", "marketer")
    html = client.get("/?status=new&reset=done").text
    assert len(ids(html)) == 6 and "Demo reset to the original data." in html


def test_post_to_the_queue_is_not_allowed(client):
    assert client.post("/", data={"status": "new"}).status_code == 405


# ---- hand-edited URLs ----

HOSTILE = [
    "?status=bogus",
    "?status=NEW",
    "?status=",
    "?status=%27%20OR%201%3D1%20--",
    "?status=new%3BDROP%20TABLE%20submission",
    "?status=%3Cscript%3Ealert(1)%3C%2Fscript%3E",
    "?product=%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E",
    "?channel=" + "x" * 10000,
    "?status%5B%5D=x",                      # status[]=x
    "?status=new&status=approved",          # repeated key
    "?status=new&status=new",
    "?status=%00",
    "?status=%F0%9F%98%80%E2%80%AE",
    "?unknown=1&status=bogus&product=nope",
]


@pytest.mark.parametrize("query", HOSTILE)
def test_hand_edited_urls_return_all_and_never_reflect_the_payload(client, query):
    r = client.get("/" + query)
    assert r.status_code == 200
    html = r.text
    assert ids(html) == EXPECTED_ORDER
    for name in ("status", "product", "channel"):
        assert selected(html, name) == [""], (query, name)
    for payload in ("OR 1=1", "DROP TABLE", "alert(1)", "xxxxxxxxxx", "onerror", "\u202E", "bogus", "nope"):
        assert payload not in html, (query, payload)


def test_a_valid_filter_next_to_a_bad_one_still_applies(client):
    html = client.get("/?status=new&product=%27%20OR%201%3D1").text
    assert len(ids(html)) == 6 and selected(html, "status") == ["new"]


def test_filters_from_query_unit():
    f = filters_from_query(QueryParams("status=new&product=loan&channel=display"))
    assert f == {"status": "new", "product": "loan", "channel": "display", "source": None}
    assert filters_from_query(QueryParams("")) == {"status": None, "product": None, "channel": None, "source": None}
    assert filters_from_query(QueryParams("status=new&status=new"))["status"] is None
    assert filters_from_query(QueryParams("status[]=new"))["status"] is None


# ---- summary line ----

def test_unfiltered_summary_counts_items(client):
    assert summary(client.get("/").text).startswith("14 items")


def test_summary_singular_and_empty(client):
    assert summary(client.get("/?status=rejected").text) == "1 item · none need attention"
    assert summary(client.get("/?status=rejected&product=card").text) == "0 items"


def test_summary_text_unit():
    def v(n, a):
        return [{"needs_attention": i < a} for i in range(n)]
    assert summary_text(v(0, 0)) == "0 items"
    assert summary_text(v(1, 0)) == "1 item · none need attention"
    assert summary_text(v(1, 1)) == "1 item · 1 needs attention"
    assert summary_text(v(5, 1)) == "5 items · 1 needs attention"
    assert summary_text(v(14, 4)) == "14 items · 4 need attention"


def test_attention_counts_only_open_urgent_items(client):
    # Approved and rejected items are never urgent, however near their date.
    assert summary(client.get("/?status=approved").text) == "3 items · none need attention"
    assert summary(client.get("/?status=rejected").text).endswith("none need attention")
    assert "1 needs attention" in summary(client.get("/?status=in_review").text)   # #11 overdue


# #14 launches 3 days out: rush only if that is within 2 business days. #11, #1 and #2 are urgent every day.
ATTENTION_BY_WEEKDAY = {0: 3, 1: 3, 2: 4, 3: 4, 4: 4, 5: 4, 6: 3}   # Mon..Sun


@pytest.mark.parametrize("day", range(7))
def test_need_attention_count_on_every_weekday(db_path, monkeypatch, day):
    from app.main import app
    when = datetime(2026, 10, 5 + day, 12, 0, tzinfo=timezone.utc)   # 2026-10-05 is a Monday
    assert when.weekday() == day
    monkeypatch.setattr(clock, "now", lambda: when)
    with TestClient(app) as c:
        text = summary(c.get("/").text)
    n = ATTENTION_BY_WEEKDAY[day]
    assert text == "14 items · %d need attention" % n


# ---- markup safety ----

def test_no_inline_styles_or_scripts_on_the_filtered_page(client):
    html = client.get("/?status=new").text
    assert not re.search(r"\sstyle\s*=", html) and not re.search(r"<script(?![^>]*\bsrc=)", html)


def test_filtered_page_headers(client):
    r = client.get("/?status=new")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_filters_follow_the_column_order_and_sort_comes_last(client):
    form = re.search(r'<form class="filters".*?</form>', client.get("/").text, re.S).group(0)
    names = re.findall(r'<select id="filter-(\w+)"', form)
    assert names == ["product", "channel", "status", "source", "sort"]


def test_sort_by_launch_date_latest_first_reverses_the_queue(client):
    def order(url):
        return [int(i) for i in re.findall(r'href="/review/(\d+)', client.get(url).text)]
    asc, desc = order("/"), order("/?sort=launch_desc")
    assert asc[0] == 11 and desc[0] == 12
    assert "Launch date (latest first)" in client.get("/?sort=launch_desc").text
    assert order("/?sort=bogus") == asc and order("/?sort=launch_desc&sort=launch_asc") == asc


def test_sort_by_flags_orders_by_open_flag_count(client):
    def counts(url):
        html = client.get(url).text
        return [int(c) for c in re.findall(r'<span class="flag-count">(\d+)</span>', html)]
    most, fewest = counts("/?sort=flags_desc"), counts("/?sort=flags_asc")
    assert most == sorted(most, reverse=True) and most[0] > most[-1]
    assert fewest == sorted(fewest) and sorted(most) == fewest
    assert "Flags: most first" in client.get("/").text
