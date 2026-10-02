from datetime import date, datetime, timedelta, timezone

import pytest

from app import clock
from app.clock import business_days_until, urgency

D = date.fromisoformat
FRI = D("2026-10-02")  # a Friday
assert FRI.weekday() == 4


@pytest.mark.parametrize("today,launch,expected", [
    ("2026-10-02", "2026-10-02", "rush"),       # launch today
    ("2026-10-02", "2026-10-01", "overdue"),    # yesterday
    ("2026-10-01", "2026-10-02", "rush"),       # +1 business day
    ("2026-10-01", "2026-10-05", "rush"),       # Thu -> Mon is 2
    ("2026-10-01", "2026-10-06", None),         # Thu -> Tue is 3
    ("2026-10-02", "2026-10-03", "rush"),       # Fri -> Sat is 0
    ("2026-10-02", "2026-10-04", "rush"),       # Fri -> Sun is 0
    ("2026-10-02", "2026-10-05", "rush"),       # Fri -> Mon is 1
    ("2026-10-02", "2026-10-07", None),         # Fri -> Wed is 3
    ("2026-10-02", "2026-10-06", "rush"),       # Fri -> Tue is 2
    ("2026-10-03", "2026-10-05", "rush"),       # Sat -> Mon is 1
    ("2026-10-04", "2026-10-05", "rush"),       # Sun -> Mon is 1
    ("2026-10-04", "2026-10-07", None),         # Sun -> Wed is 3
    ("2026-12-31", "2027-01-02", "rush"),       # year boundary
    ("2028-02-28", "2028-03-01", "rush"),       # leap day in between
    ("2028-03-01", "2028-02-29", "overdue"),    # leap day yesterday
])
def test_urgency_table(today, launch, expected):
    assert urgency(today, launch, "new") == expected


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_final_statuses_are_never_urgent(status):
    assert urgency("2026-10-02", "2026-01-01", status) is None  # long past
    assert urgency("2026-10-02", "2026-10-02", status) is None  # today


@pytest.mark.parametrize("status", ["new", "in_review", "changes_requested"])
def test_open_statuses_are_urgent(status):
    assert urgency("2026-10-02", "2026-10-01", status) == "overdue"
    assert urgency("2026-10-02", "2026-10-02", status) == "rush"


def test_accepts_dates_and_iso_strings():
    assert urgency(D("2026-10-01"), "2026-10-02", "new") == "rush"
    assert urgency("2026-10-01", D("2026-10-02"), "new") == "rush"


def test_business_days_counts():
    assert business_days_until("2026-10-02", "2026-10-02") == 0
    assert business_days_until("2026-10-02", "2026-10-09") == 5   # one full week
    assert business_days_until("2026-10-02", "2026-10-16") == 10
    assert business_days_until("2026-10-02", "2026-10-01") == 0   # past is zero, not negative


def test_business_days_matches_a_naive_loop_for_every_start_and_length():
    start = D("2026-10-01")
    for s in range(7):
        today = start + timedelta(days=s)
        for n in range(0, 60):
            launch = today + timedelta(days=n)
            naive = sum(1 for i in range(1, n + 1) if (today + timedelta(days=i)).weekday() < 5)
            assert business_days_until(today, launch) == naive


def test_far_future_date_is_instant_and_correct():
    assert business_days_until("2026-10-05", "9999-12-31") > 0  # would hang a day-by-day loop


@pytest.mark.parametrize("offset,expected", [(-1, "overdue"), (1, "rush"), (2, "rush")])
def test_seed_invariants_hold_on_every_weekday(offset, expected):
    for s in range(7):
        today = D("2026-10-05") + timedelta(days=s)
        assert urgency(today, today + timedelta(days=offset), "new") == expected


def test_later_seed_items_are_never_rush_on_any_weekday():
    for s in range(7):
        today = D("2026-10-05") + timedelta(days=s)
        for offset in (5, 6, 8, 9, 20, 25, 30):
            assert urgency(today, today + timedelta(days=offset), "new") is None


@pytest.mark.parametrize("bad", [None, "", "not a date", "2026-13-01", "2026-02-30", 20261002, 1.5, [], "2026-10-02T00:00:00"])
def test_invalid_dates_raise_value_error(bad):
    with pytest.raises(ValueError):
        urgency("2026-10-02", bad, "new")
    with pytest.raises(ValueError):
        urgency(bad, "2026-10-02", "new")
    with pytest.raises(ValueError):
        business_days_until(bad, "2026-10-02")


def test_datetime_is_rejected_not_silently_used():
    with pytest.raises(ValueError):
        urgency(datetime(2026, 10, 2, tzinfo=timezone.utc), "2026-10-02", "new")


@pytest.mark.parametrize("bad", [None, "", "bogus", "NEW", "approved "])
def test_unknown_status_raises(bad):
    with pytest.raises(ValueError):
        urgency("2026-10-02", "2026-10-02", bad)


def test_now_is_timezone_aware_utc():
    n = clock.now()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(0)


def test_today_follows_now_so_tests_can_freeze_time(monkeypatch):
    frozen = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: frozen)
    assert clock.today() == D("2026-12-31")


def test_importing_clock_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib
    importlib.reload(clock)
    assert list(tmp_path.iterdir()) == []
