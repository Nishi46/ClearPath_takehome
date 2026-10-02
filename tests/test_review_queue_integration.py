import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest

from app import db, queue
from app.review import safe_back, with_back
from tests.test_queue_page import body_rows, row_for
from tests.test_review_decision_route import post


def queue_row(client, sid, query=""):
    return row_for(client.get("/" + query).text, sid)


def back_href(html):
    return unescape(re.search(r'<p class="review-back"><a href="([^"]*)">', html).group(1))


# ---- a decision shows up in the queue ----

def test_approving_3_updates_only_its_row(client):
    before = {sid: re.sub(r"\s+", " ", queue_row(client, sid)) for sid in range(1, 15)}
    assert post(client, 3).status_code == 303
    after = {sid: re.sub(r"\s+", " ", queue_row(client, sid)) for sid in range(1, 15)}
    assert "Approved" in after[3] and "In review" not in after[3]
    assert "urgency" not in after[3] and "Rush" not in after[3] and "Overdue" not in after[3]
    assert [s for s in range(1, 15) if before[s] != after[s]] == [3]


def test_flag_count_is_unchanged_by_a_decision(client):
    def flags(sid):
        return re.search(r'data-label="Flags".*?</td>', queue_row(client, sid), re.S).group(0)
    before = flags(3)
    post(client, 3)
    assert flags(3) == before


def test_request_changes_and_reject_show_in_the_queue(client):
    post(client, 2, outcome="changes_requested", reason="Fix it.")
    post(client, 4, outcome="rejected", reason="No.")
    assert "Changes requested" in queue_row(client, 2) and "Rejected" in queue_row(client, 4)


def test_deciding_an_overdue_item_removes_its_overdue_label(client):
    assert "Overdue" in queue_row(client, 11)                      # #11 launched yesterday, in review
    post(client, 11)
    row = queue_row(client, 11)
    assert "Approved" in row and "Overdue" not in row and "urgency-overdue" not in row


def test_a_rush_item_loses_rush_when_decided_but_keeps_it_when_changes_are_requested(client):
    assert "urgency-rush" in queue_row(client, 1)
    post(client, 1, outcome="changes_requested", reason="r")
    assert "urgency-rush" in queue_row(client, 1)                  # still open: the marketer must act
    post(client, 2)
    assert "urgency-rush" not in queue_row(client, 2)


def test_filtering_by_status_follows_a_decision(client):
    assert 'href="/review/3' in client.get("/?status=in_review").text
    post(client, 3)
    assert 'href="/review/3' not in client.get("/?status=in_review").text
    assert 'href="/review/3' in client.get("/?status=approved").text


def test_row_links_and_keyboard_target_still_reach_the_page(client):
    html = client.get("/").text
    for sid in re.findall(r'<a href="/review/(\d+)">', html):
        assert client.get(f"/review/{sid}").status_code == 200


# ---- back link: safe_back is the only gate ----

@pytest.mark.parametrize("raw, expected", [
    ("/?status=in_review&product=loan", "/?status=in_review&product=loan"),
    ("/?product=loan&status=in_review", "/?status=in_review&product=loan"),            # canonical order
    ("/?channel=email", "/?channel=email"),
    ("/?status=in_review&status=approved", "/"),                                        # ambiguous: dropped
    ("/?status=bogus&product=card", "/?product=card"),
    ("/?status=in_review&evil=1&back=/x", "/?status=in_review"),                        # unknown keys dropped
    ("/?", "/"), ("/", "/"), ("", "/"),
    ("//evil.example", "/"), ("https://evil.example", "/"), ("/\\evil.example", "/"),
    ("javascript:alert(1)", "/"), ("/?status=<script>", "/"), ("/?status=in_review#x", "/"),
    ("/?status=in_review%0d%0aSet-Cookie:x=1", "/"), ("/?" + "a=1&" * 50, "/"), ("/?status=" + "x" * 5000, "/"),
    ("http://evil/?status=in_review", "/"), ("  /?status=in_review", "/"), ("/?%73tatus=in_review", "/?status=in_review"),
    (None, "/"), (5, "/"), (["/?status=new"], "/"), (b"/?status=new", "/"),
])
def test_safe_back(raw, expected):
    assert safe_back(raw) == expected


def test_safe_back_output_is_always_a_local_queue_url():
    for raw in ("/?status=new", "/?status=new&product=loan&channel=email", "/?x=%2F%2Fevil.example", "//x"):
        out = safe_back(raw)
        assert out == "/" or (out.startswith("/?") and "//" not in out and ":" not in out.split("?")[0])


def test_with_back():
    assert with_back("/review/3", "/") == "/review/3"
    assert with_back("/review/3", "/?status=new") == "/review/3?back=%2F%3Fstatus%3Dnew"
    assert with_back("/review/3?v=1", "/?status=new") == "/review/3?v=1&back=%2F%3Fstatus%3Dnew"


# ---- the queue links carry the filters; the review page honors them ----

def test_filtered_queue_links_carry_the_filters(client):
    html = client.get("/?status=in_review&product=loan").text
    link = unescape(re.search(r'<a href="(/review/\d+[^"]*)"', html).group(1))
    assert parse_qs(urlparse(link).query)["back"] == ["/?status=in_review&product=loan"]
    assert 'href="/review/' in html


def test_unfiltered_queue_links_are_plain(client):
    assert set(re.findall(r'<a href="(/review/\d+[^"]*)"', client.get("/").text)) == {f"/review/{i}" for i in range(1, 15)}


def test_bad_filters_on_the_queue_do_not_leak_into_links(client):
    html = client.get("/?status=bogus&product=<script>&channel=email").text
    links = re.findall(r'<a href="(/review/\d+[^"]*)"', html)
    assert links and all("script" not in l and "bogus" not in l for l in links)
    assert all(unescape(l).endswith("back=%2F%3Fchannel%3Demail") for l in links)


def test_back_link_returns_to_the_filtered_queue(client):
    link = unescape(re.search(r'<a href="(/review/\d+[^"]*)"', client.get("/?status=in_review&product=loan").text).group(1))
    page = client.get(link).text
    assert back_href(page) == "/?status=in_review&product=loan"
    assert client.get(back_href(page)).status_code == 200


def test_plain_review_page_goes_back_to_the_plain_queue(client):
    assert back_href(client.get("/review/3").text) == "/"


@pytest.mark.parametrize("raw", ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)",
                                 "/?status=<script>", "x" * 5000, "", "/?status=in_review&status=new"])
def test_hostile_back_values_fall_back_to_the_queue(client, raw):
    r = client.get("/review/3", params={"back": raw})
    assert r.status_code == 200 and back_href(r.text) == "/"
    assert "evil.example" not in r.text and "<script>alert" not in r.text


def test_a_repeated_back_parameter_is_ignored(client):
    r = client.get("/review/3?back=/?status=new&back=/?status=approved")
    assert back_href(r.text) == "/"


def test_valid_back_is_escaped_and_rebuilt(client):
    r = client.get("/review/3", params={"back": "/?status=in_review&evil=<b>x</b>"})
    assert back_href(r.text) == "/?status=in_review" and "<b>x</b>" not in r.text


def test_version_links_and_older_notice_keep_the_back_target(client):
    r = client.get("/review/7", params={"back": "/?status=approved"})
    links = [unescape(h) for h in re.findall(r'class="version-link|href="(/review/7[^"]*)"', r.text) if h]
    assert links and all("back=" in l for l in links)
    old = client.get("/review/7", params={"v": "1", "back": "/?status=approved"}).text
    assert re.search(r'href="/review/7\?back=%2F%3Fstatus%3Dapproved">Go to v2', old)


def test_the_decision_keeps_the_back_target(client):
    page = client.get("/review/3", params={"back": "/?status=in_review"}).text
    assert '<input type="hidden" name="back" value="/?status=in_review">' in page
    r = post(client, data={"outcome": "approved", "version": "1", "back": "/?status=in_review"})
    assert r.status_code == 303 and r.headers["location"] == "/review/3?back=%2F%3Fstatus%3Din_review"


@pytest.mark.parametrize("sid, raw", [(1, "https://evil.example"), (2, "//evil.example"),
                                       (4, "/?status=<script>"), (9, "javascript:1")])
def test_a_hostile_back_in_the_decision_form_is_neutralised(client, sid, raw):
    r = post(client, sid=sid, data={"outcome": "approved", "version": "1", "back": raw})
    assert r.status_code == 303 and r.headers["location"] == f"/review/{sid}"     # back is dropped entirely


def test_a_refused_decision_keeps_the_back_link(client):
    post(client, 3)
    r = post(client, data={"outcome": "approved", "version": "1", "back": "/?status=in_review"})
    assert r.status_code == 409 and back_href(r.text) == "/?status=in_review"


def test_no_form_means_no_back_field(client):
    assert 'name="back"' not in client.get("/review/3").text
    html = client.get("/review/6", params={"back": "/?status=approved"}).text
    assert 'name="outcome"' not in html  # no decision form, so no decision form back field
    assert html.count('name="back"') == 1 and html.index('name="back"') > html.index('class="comment-form"')
