"""Phase 7 steps 6 to 9: submit-form errors, very long copy, launch dates, unchanged resubmits."""
import re
import time
from datetime import date, timedelta
from html import unescape

import pytest
from fastapi.testclient import TestClient

from app import clock, db, diff, rules, submit
from tests.conftest import WEEK, pin_clock
from tests.test_phase7_edge_matrix import as_role, counts, good_form, stored_copy
from tests.test_queue_page import row_for

HEADERS = {"origin": "http://testserver"}
HOSTILE = '"><img src=x onerror=alert(1)></textarea><script>alert(1)</script>'


@pytest.fixture
def maya(client):
    return as_role(client, "marketer", "Maya Chen")


@pytest.fixture
def jordan(client):
    return as_role(client, "marketer", "Jordan Lee")


def post(c, data=None, **over):
    return c.post("/submit", data=good_form(**over) if data is None else data, headers=HEADERS,
                  follow_redirects=False)


def field_errors(html):
    return dict(re.findall(r'<p class="field-error" id="error-(\w+)">(.*?)</p>', html))


# ---- step 6: missing fields and blank copy ----

FIELD_MESSAGES = {"title": "Add a title.", "product": "Choose a product.", "channel": "Choose a channel.",
                  "launch_date": "Choose a launch date.", "copy": "Copy can't be empty or only spaces."}


@pytest.mark.parametrize("field,message", FIELD_MESSAGES.items())
def test_each_missing_field_gives_only_its_error_and_keeps_the_rest(maya, field, message):
    before = counts()
    r = post(maya, **{field: ""})
    assert r.status_code == 422 and counts() == before
    errors = field_errors(r.text)
    assert list(errors) == [field] and unescape(errors[field]) == message
    for other, value in good_form().items():
        if other not in (field, "copy", "notes", "product", "channel"):
            assert 'value="%s"' % value in r.text


def test_error_summary_links_to_the_first_field_and_takes_focus(maya):
    r = post(maya, title="", copy="")
    assert 'role="alert" tabindex="-1" autofocus' in r.text and "Fix 2 things below" in r.text
    assert 'href="#field-title"' in r.text and 'id="field-title"' in r.text


@pytest.mark.parametrize("blank", ["", " ", "\t\n  \n", "​​", "   ", "﻿", "⁠ ‍",
                                   "\r\n\r\n"])
def test_blank_copy_is_refused_and_nothing_is_written(maya, blank):
    before = counts()
    r = post(maya, copy=blank, title="Keep me")
    assert r.status_code == 422 and "copy" in field_errors(r.text) and counts() == before
    assert 'value="Keep me"' in r.text


@pytest.mark.parametrize("field,value", [("product", "crypto"), ("product", "Loan"), ("product", "loan "),
                                         ("channel", "fax"), ("channel", "EMAIL"), ("product", "' OR 1=1"),
                                         ("product", "__proto__")])
def test_a_value_outside_the_allowlist_is_a_field_error(maya, field, value):
    before = counts()
    r = post(maya, **{field: value})
    assert r.status_code == 422 and field in field_errors(r.text) and counts() == before


@pytest.mark.parametrize("field,value,key", [
    ("title", "t" * (submit.MAX_TITLE_CHARS + 1), "title"), ("title", "two\nlines", "title"),
    ("title", "bell\x07", "title"), ("copy", "c" * (submit.MAX_COPY_CHARS + 1), "copy"),
    ("copy", "nul\x00byte", "copy"), ("notes", "n" * (submit.MAX_NOTES_CHARS + 1), "notes"),
    ("launch_date", "2026-13-45", "launch_date"), ("launch_date", "0000-00-00", "launch_date"),
    ("launch_date", "tomorrow", "launch_date"), ("launch_date", "9999-12-31", "launch_date"),
    ("launch_date", "2027-02-29", "launch_date"), ("launch_date", "2026-1-5", "launch_date"),
    ("launch_date", "２０２６-10-10", "launch_date"), ("launch_date", "1999-01-01", "launch_date"),
])
def test_bad_values_get_a_specific_message_and_write_nothing(maya, field, value, key):
    before = counts()
    r = post(maya, **{field: value})
    assert r.status_code == 422 and key in field_errors(r.text) and counts() == before


@pytest.mark.parametrize("field,value", [("title", "T" * submit.MAX_TITLE_CHARS),
                                         ("copy", "Apply. " * 1400 + "x"),
                                         ("launch_date", "2028-02-29")])
def test_boundary_values_are_accepted(maya, field, value):
    if field == "copy":
        value = value[:submit.MAX_COPY_CHARS]
    assert post(maya, **{field: value}).status_code in (303, 409)


def test_repeated_fields_use_no_value_and_never_crash(maya):
    before = counts()
    r = maya.post("/submit", content="title=a&title=b&product=loan&channel=email&copy=x&launch_date=2099-01-01",
                  headers={**HEADERS, "content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert r.status_code == 422 and "title" in field_errors(r.text) and counts() == before


@pytest.mark.parametrize("field", ["title", "copy", "notes", "launch_date", "product", "channel"])
def test_hostile_text_is_escaped_when_the_form_comes_back(maya, field):
    r = post(maya, **{field: HOSTILE, "title": HOSTILE[:100] if field != "title" else HOSTILE, "copy": ""})
    assert r.status_code == 422
    assert "<script>alert(1)" not in r.text and "<img src=x" not in r.text and 'onerror=alert' not in re.sub(
        r"&lt;[^&]*", "", r.text).replace("&quot;", "")


def test_reviewer_cannot_post_a_submission(client):
    before = counts()
    r = as_role(client, "reviewer").post("/submit", data=good_form(), headers=HEADERS, follow_redirects=False)
    assert r.status_code == 403 and counts() == before


def test_cross_origin_post_is_refused(maya):
    before = counts()
    for headers in ({"origin": "https://evil.example"}, {"origin": "null"}, {"referer": "https://evil.example/x"}):
        r = maya.post("/submit", data=good_form(), headers=headers, follow_redirects=False)
        assert r.status_code == 403
    assert counts() == before


def test_form_cannot_set_the_submitter_status_or_version(maya):
    r = maya.post("/submit", data=dict(good_form(), submitted_by="Jordan Lee", status="approved",
                                       current_version="9", id="999"), headers=HEADERS, follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT submitted_by, status, current_version, id FROM submission ORDER BY id DESC").fetchone()
    assert tuple(row)[:3] == ("Maya Chen", "new", 1) and row[3] != 999


# ---- step 7: very long copy ----

def test_12_renders_quickly_and_within_a_size_cap(client):
    start = time.perf_counter()
    r = client.get("/review/12")
    assert r.status_code == 200 and time.perf_counter() - start < 1.0 and len(r.content) < 400_000


def test_copy_of_exactly_the_maximum_is_accepted_and_one_more_is_not(maya):
    at_max = ("Apply today. " * 1000)[:submit.MAX_COPY_CHARS]
    assert post(maya, copy=at_max).status_code == 303
    r = post(maya, copy=at_max + "x", title="Another")
    assert r.status_code == 422 and "copy" in field_errors(r.text)


def test_the_copy_pane_wraps_long_unbroken_words():
    css = open("app/static/style.css").read()
    assert re.search(r"overflow-wrap:\s*(anywhere|break-word)|word-break:\s*break-(all|word)", css)


def test_a_maximum_length_unbroken_word_is_stored_and_shown_without_error(maya, client):
    word = "A" * submit.MAX_COPY_CHARS
    r = post(maya, copy=word)
    assert r.status_code == 303
    sid = int(r.headers["location"].split("=")[1])
    assert as_role(client, "reviewer").get(f"/review/{sid}").status_code == 200


@pytest.mark.parametrize("text", ["café Guaranteed approval", "\U0001F600\U0001F600 Guaranteed approval",
                                  "שלום Guaranteed approval", "é Guaranteed approval"])
def test_highlight_offsets_land_on_the_right_characters_for_multibyte_text(text):
    found = [f for f in rules.evaluate("loan", "email", text) if f.rule_id == "R1"]
    assert found
    for f in found:
        assert text[f.start:f.end] == f.matched_text and "uaranteed" in f.matched_text


def test_pathological_near_matches_finish_within_a_time_budget():
    # Every phrase rule fed 10,000 characters of repeated near-misses, in every product and channel.
    nasty = ["guarantee " * 1000, "no credit check " * 700, "pre-approved " * 700, "you're " * 1400,
             "a" * 10_000, "% rate " * 1400, "limited time " * 800, " " * 5000 + "x" * 5000]
    start = time.perf_counter()
    for text in nasty:
        for product in ("loan", "card", "mortgage"):
            for channel in ("email", "paid_social", "display", "affiliate_page"):
                rules.evaluate(product, channel, text)
    assert time.perf_counter() - start < 5.0


def test_diff_of_two_maximum_length_versions_is_bounded():
    a = " ".join("word%d" % i for i in range(2000))[:submit.MAX_COPY_CHARS]
    b = " ".join("other%d" % i for i in range(2000))[:submit.MAX_COPY_CHARS]
    start = time.perf_counter()
    result = diff.diff_text(a, b)
    assert time.perf_counter() - start < 3.0 and result


def test_diff_of_two_repetitive_maximum_length_versions_is_bounded():
    # A few words repeated thousands of times is the quadratic case for a naive diff.
    a, b = ("buy now " * 1250)[:submit.MAX_COPY_CHARS], ("buy later " * 1000)[:submit.MAX_COPY_CHARS]
    start = time.perf_counter()
    assert diff.diff_text(a, b)
    assert time.perf_counter() - start < 3.0


# ---- step 8: launch dates ----

PAIRS = [  # (today weekday, launch offset in days) -> expected
    ("monday", -1), ("monday", 0), ("monday", 1), ("monday", 2), ("monday", 3), ("monday", 4), ("monday", 7),
    ("wednesday", 0), ("wednesday", 1), ("wednesday", 2), ("wednesday", 3), ("wednesday", 5),
    ("thursday", 4), ("thursday", 5), ("friday", 1), ("friday", 2), ("friday", 3), ("friday", 4), ("friday", 5),
    ("saturday", 2), ("saturday", 3), ("sunday", 1), ("sunday", 3), ("sunday", 4),
]


@pytest.mark.parametrize("day,offset", PAIRS)
def test_one_rule_drives_the_queue_label_and_the_form_warning(day, offset, monkeypatch):
    today = date.fromisoformat(WEEK[day])
    launch = today + timedelta(days=offset)
    urgent = clock.urgency(today, launch, "new")
    warned = submit.launch_warnings(launch, today)
    assert (urgent == "overdue") == (warned == ["launch_past"])
    assert (urgent == "rush") == (warned == ["launch_rush"])
    assert (urgent is None) == (warned == [])


@pytest.mark.parametrize("day", list(WEEK))
@pytest.mark.parametrize("sid", [6, 7, 13])
def test_decided_items_are_never_urgent_on_any_weekday(day, sid, monkeypatch, db_path):
    from app.main import app

    pin_clock(monkeypatch, day)
    with TestClient(app) as c:
        row = row_for(c.get("/").text, sid)
    assert "urgency" not in row


@pytest.mark.parametrize("day", ["monday", "wednesday", "friday"])
def test_form_warning_matches_the_label_in_words(day, monkeypatch, db_path):
    from app.main import app

    today = pin_clock(monkeypatch, day).date()
    with TestClient(app) as c:
        as_role(c, "marketer", "Maya Chen")
        for offset, text in ((-2, "already passed"), (1, "rush review")):
            r = post(c, launch_date=(today + timedelta(days=offset)).isoformat(), title="T%d" % offset)
            assert r.status_code == 303
            page = c.get(r.headers["location"]).text
            assert text in page or "Warning" in page


def test_a_warning_and_a_validation_error_both_show(maya):
    past = (clock.today() - timedelta(days=2)).isoformat()
    r = post(maya, launch_date=past, copy="")
    assert r.status_code == 422 and "copy" in field_errors(r.text)
    assert "already passed" in r.text


def test_the_warning_never_replaces_the_launch_error(maya):
    r = post(maya, launch_date="9999-12-31")
    assert "launch_date" in field_errors(r.text) and "already passed" not in r.text


@pytest.mark.parametrize("hour,minute", [(23, 30), (0, 30)])
def test_the_label_uses_the_utc_date_at_both_ends_of_the_day(hour, minute, monkeypatch, db_path):
    from app.main import app

    moment = pin_clock(monkeypatch, "wednesday", hour, minute)
    assert clock.today() == moment.date()
    with TestClient(app) as c:
        assert "urgency-overdue" in row_for(c.get("/").text, 11)
        assert "urgency-rush" in row_for(c.get("/").text, 1)


# ---- step 9: resubmit without changes ----

def resubmit(c, copy, sid=14, base="1", **over):
    data = {"copy": copy, "notes": "", "base_version": base,
            "launch_date": (clock.today() + timedelta(days=30)).isoformat()}
    data.update(over)
    return c.post("/resubmit/%d" % sid, data=data, headers=HEADERS, follow_redirects=False)


@pytest.mark.parametrize("variant", [
    lambda s: s, lambda s: s.replace("\n", "\r\n"), lambda s: s + "   ", lambda s: "\n\n" + s + "\n\n",
    lambda s: s.replace("\n", "\r"), lambda s: "  " + s + " \t \r\n",
])
def test_unchanged_copy_is_blocked_however_it_is_dressed(jordan, variant):
    before = counts()
    r = resubmit(jordan, variant(stored_copy(14)))
    assert r.status_code == 422 and counts() == before
    assert "Nothing has changed in the copy" in r.text


@pytest.mark.parametrize("edit", [
    lambda s: s + "!", lambda s: s.replace("a", "A", 1), lambda s: s.replace(".", ",", 1), lambda s: s + " .",
])
def test_any_real_change_is_accepted_as_exactly_one_new_version(jordan, edit):
    before = counts()
    r = resubmit(jordan, edit(stored_copy(14)))
    assert r.status_code == 303
    after = counts()
    assert after["version"] == before["version"] + 1 and after["submission"] == before["submission"]
    with db.connect() as c:
        assert c.execute("SELECT current_version FROM submission WHERE id = 14").fetchone()[0] == 2


def test_the_unchanged_check_is_a_server_check_a_hand_made_post_hits_too(jordan):
    # No form page was ever loaded: this is a raw POST, as a script would send it.
    before = counts()
    assert resubmit(jordan, stored_copy(14)).status_code == 422 and counts() == before


def test_unchanged_message_keeps_what_was_typed(jordan):
    r = resubmit(jordan, stored_copy(14) + "   ")
    assert unescape(re.search(r'<textarea id="field-copy"[^>]*>\n(.*?)</textarea>', r.text, re.S).group(1)).strip() \
        == stored_copy(14)


def test_not_the_owner_is_refused_and_writes_nothing(maya):
    before = counts()
    assert resubmit(maya, stored_copy(14) + "!").status_code == 403 and counts() == before


@pytest.mark.parametrize("sid", [6, 3, 1])
def test_items_that_cannot_be_resubmitted_are_refused(jordan, client, sid):
    # #6 approved (locked); #3 and #1 have no decision yet. Whoever owns them, nothing is written.
    owner = {6: "Maya Chen", 3: "Maya Chen", 1: "Maya Chen"}[sid]
    as_role(client, "marketer", owner)
    before = counts()
    r = resubmit(client, "Entirely new copy. Subject to credit approval.", sid=sid)
    assert r.status_code == 409 and counts() == before


def test_a_stale_base_version_is_refused(jordan):
    before = counts()
    r = resubmit(jordan, stored_copy(14) + "!", base="2")
    assert r.status_code == 409 and counts() == before
