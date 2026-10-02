from datetime import date, datetime, timezone

import pytest

from app import queue as queue_module
from app import seed
from app.queue import FILTER_OPTIONS, list_queue, row_view
from app.seed import seed_all

THU = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)  # a Thursday


@pytest.fixture
def views(conn):
    seed_all(conn, NOW)
    return {v["id"]: v for v in (row_view(r, THU) for r in list_queue(conn))}


def make(**kw):
    row = {"id": 1, "title": "T", "product": "loan", "channel": "email", "launch_date": "2026-10-02",
           "status": "new", "submitted_by": "Maya Chen", "created_at": "x", "version_number": 1, "flag_count": 0,
           "top_severity": None}
    row.update(kw)
    return row


# ---- the seed on a Thursday ----

def test_seed_urgency_on_a_thursday(views):
    assert views[11]["urgency_label"] == "Overdue by 1 day"
    assert views[1]["urgency_label"] == "Rush: launches tomorrow"
    assert views[2]["urgency_label"] == "Rush: launches in 1 business day"   # Saturday: only Friday in between
    assert views[10]["urgency_label"] == "" and views[10]["urgency"] is None


def test_only_the_expected_rows_are_urgent_on_a_thursday(views):
    urgent = {i for i, v in views.items() if v["urgency"]}
    assert urgent == {11, 1, 2, 14}   # #14 launches Sunday, so it is rush on a Thursday
    assert {i for i, v in views.items() if v["needs_attention"]} == urgent


def test_final_statuses_never_show_urgency_even_when_near(views):
    for sid in (13, 6, 7, 8):   # approved and rejected
        assert views[sid]["urgency"] is None and views[sid]["urgency_label"] == ""
    assert views[13]["launch_text"] == "Oct 10, 2026"   # +9 days; the date still shows


def test_an_approved_item_with_a_past_date_shows_no_urgency():
    v = row_view(make(status="approved", launch_date="2026-01-01"), THU)
    assert v["urgency"] is None and v["urgency_label"] == "" and v["launch_text"] == "Jan 1, 2026"


# ---- label wording ----

@pytest.mark.parametrize("launch,expected", [
    ("2026-09-30", "Overdue by 1 day"),
    ("2026-09-29", "Overdue by 2 days"),
    ("2025-10-01", "Overdue by 365 days"),
    ("2026-10-01", "Rush: launches today"),
    ("2026-10-02", "Rush: launches tomorrow"),
    ("2026-10-03", "Rush: launches in 1 business day"),    # Thu -> Sat
    ("2026-10-05", "Rush: launches in 2 business days"),   # Thu -> Mon
])
def test_label_wording_and_pluralization(launch, expected):
    assert row_view(make(launch_date=launch), THU)["urgency_label"] == expected


def test_weekend_launch_from_a_friday_reads_naturally():
    fri = date(2026, 10, 2)
    assert row_view(make(launch_date="2026-10-04"), fri)["urgency_label"] == "Rush: launches this weekend"
    assert row_view(make(launch_date="2026-10-03"), fri)["urgency_label"] == "Rush: launches tomorrow"


def test_not_urgent_beyond_two_business_days():
    v = row_view(make(launch_date="2026-10-06"), THU)   # Thu -> Tue is 3
    assert v["urgency"] is None and v["urgency_class"] == "" and not v["needs_attention"]


@pytest.mark.parametrize("kind,launch", [("overdue", "2026-09-01"), ("rush", "2026-10-02")])
def test_css_class_matches_the_kind(kind, launch):
    v = row_view(make(launch_date=launch), THU)
    assert v["urgency"] == kind and v["urgency_class"] == "urgency-" + kind


def test_the_date_is_always_shown_next_to_the_label():
    v = row_view(make(launch_date="2026-10-02"), THU)
    assert v["launch_text"] == "Oct 2, 2026" and v["launch_date"] == "2026-10-02"


@pytest.mark.parametrize("iso,text", [("2026-01-05", "Jan 5, 2026"), ("2026-12-31", "Dec 31, 2026"),
                                      ("2028-02-29", "Feb 29, 2028")])
def test_date_text_is_locale_independent(iso, text):
    assert row_view(make(launch_date=iso), THU)["launch_text"] == text


# ---- labels ----

def test_every_database_value_has_an_explicit_label():
    # A new enum value without a label must fail here, not silently show a raw word on the page.
    assert {v for v, _ in FILTER_OPTIONS["status"]} == set(seed.STATUSES)
    assert {v for v, _ in FILTER_OPTIONS["product"]} == set(seed.PRODUCTS)
    assert {v for v, _ in FILTER_OPTIONS["channel"]} == set(seed.CHANNELS)


@pytest.mark.parametrize("status,label", [("new", "New"), ("in_review", "In review"),
                                          ("changes_requested", "Changes requested"),
                                          ("approved", "Approved"), ("rejected", "Rejected")])
def test_status_labels(status, label):
    assert row_view(make(status=status), THU)["status_label"] == label


def test_product_and_channel_labels():
    v = row_view(make(product="mortgage", channel="affiliate_page"), THU)
    assert (v["product_label"], v["channel_label"]) == ("Mortgage", "Affiliate page")
    assert row_view(make(channel="paid_social"), THU)["channel_label"] == "Paid social"


def test_status_label_is_text_and_never_the_raw_database_word(views):
    for v in views.values():
        assert "_" not in v["status_label"] and v["status_label"][0].isupper()


# ---- flag column ----

def test_flags_show_the_count():
    v = row_view(make(flag_count=3, top_severity="high"), THU)
    assert (v["flags_text"], v["flags_letter"]) == ("3", "H")
    assert row_view(make(flag_count=0), THU)["flags_text"] == "0"


# ---- robustness ----

def test_unknown_values_do_not_crash_and_stay_readable():
    v = row_view(make(status="on_hold", product="boat", channel="push_notice"), THU)
    assert v["status_label"] == "On hold" and v["product_label"] == "Boat" and v["channel_label"] == "Push notice"
    assert v["urgency"] is None   # unknown status gets no urgency rather than an error


@pytest.mark.parametrize("bad", ["not a date", "", "2026-13-40", None, "2026-10-02T00:00:00"])
def test_bad_launch_date_does_not_crash(bad):
    v = row_view(make(launch_date=bad), THU)
    assert v["urgency"] is None and v["urgency_label"] == ""
    assert v["launch_text"] == str(bad)


def test_html_characters_survive_as_plain_strings():
    # Escaping is the template's job (tested in step 13); here the strings must stay untouched.
    hostile = '<img src=x onerror=alert(1)> & "quotes"'
    v = row_view(make(title=hostile, submitted_by=hostile), THU)
    assert v["title"] == hostile and v["submitted_by"] == hostile
    assert "&lt;" not in v["title"]


def test_defaults_to_todays_date(monkeypatch):
    from app import clock
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
    assert row_view(make(launch_date="2026-09-30"))["urgency_label"] == "Overdue by 1 day"


def test_view_does_not_modify_the_row():
    row = make()
    before = dict(row)
    row_view(row, THU)
    assert row == before


def test_version_and_ids_pass_through(views):
    assert views[5]["version_number"] == 2 and views[5]["id"] == 5
    assert views[5]["submitted_by"] == "Maya Chen" and views[5]["status_label"] == "In review"


def test_version_text(views):
    assert views[5]["version_text"] == "v2" and views[7]["version_text"] == "v2"
    assert views[1]["version_text"] == "v1"
    assert row_view(make(version_number=12), THU)["version_text"] == "v12"
