import re
from datetime import datetime, timezone

import pytest

from app import clock, db, seed
from tests.conftest import WEEK, pin_clock
from tests.test_queue_page import row_for


def urgency_of(client, sid):
    row = row_for(client.get("/").text, sid)
    return "overdue" if "urgency-overdue" in row else "rush" if "urgency-rush" in row else None


def launch_date(sid):
    with db.connect() as c:
        return c.execute("SELECT launch_date FROM submission WHERE id = ?", (sid,)).fetchone()[0]


@pytest.mark.parametrize("name", list(WEEK))
def test_every_weekday_name_is_that_weekday(name):
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    assert datetime.fromisoformat(WEEK[name]).weekday() == days.index(name)


def test_the_pin_is_utc_and_whole_minutes(monkeypatch):
    moment = pin_clock(monkeypatch, "Friday", 23, 30)   # name is case-insensitive
    assert clock.now() == moment == datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)
    assert clock.today().isoformat() == "2026-10-09"


def test_unknown_weekday_name_is_an_error(monkeypatch):
    with pytest.raises(KeyError):
        pin_clock(monkeypatch, "funday")


def test_the_real_clock_is_restored_even_when_the_test_raises():
    real = clock.now
    with pytest.raises(RuntimeError):
        with pytest.MonkeyPatch.context() as mp:
            pin_clock(mp, "monday")
            assert clock.now is not real
            raise RuntimeError("boom")
    assert clock.now is real


def test_unmarked_tests_use_the_real_clock():
    assert abs((datetime.now(timezone.utc) - clock.now()).total_seconds()) < 5


@pytest.mark.weekday("monday")
def test_marker_pins_before_the_app_seeds(client):
    assert clock.today().isoformat() == WEEK["monday"]
    assert launch_date(1) == "2026-10-06" and launch_date(11) == "2026-10-04"   # +1 and -1 from the pinned day


@pytest.mark.parametrize("day,expected", [("monday", None), ("tuesday", None), ("wednesday", "rush"),
                                          ("thursday", "rush"), ("friday", "rush"), ("saturday", "rush"),
                                          ("sunday", None)])
def test_14_is_rush_only_on_some_weekdays(day, expected, request, monkeypatch, db_path):
    # #14 launches in 3 calendar days; the rule counts business days, so the weekday decides.
    from fastapi.testclient import TestClient
    from app.main import app

    pin_clock(monkeypatch, day)
    with TestClient(app) as c:
        assert urgency_of(c, 14) == expected


@pytest.mark.parametrize("day", list(WEEK))
def test_1_and_2_are_rush_and_11_is_overdue_on_every_weekday(day, monkeypatch, db_path):
    from fastapi.testclient import TestClient
    from app.main import app

    pin_clock(monkeypatch, day)
    with TestClient(app) as c:
        assert (urgency_of(c, 1), urgency_of(c, 2), urgency_of(c, 11)) == ("rush", "rush", "overdue")


@pytest.mark.weekday("monday")
def test_reset_at_another_time_uses_the_new_dates(client, at_weekday):
    assert launch_date(14) == "2026-10-08"
    at_weekday("wednesday")
    with db.connect() as c:
        seed.reset_to_seed(c)
    assert launch_date(14) == "2026-10-10" and launch_date(1) == "2026-10-08"
    assert re.search(r"urgency-rush", row_for(client.get("/").text, 14))
