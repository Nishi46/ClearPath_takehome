import copy
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.seed import format_timestamp, load_seed, resolve_times

NOW = datetime(2026, 10, 1, 12, 30, 15, 123456, tzinfo=timezone.utc)  # a Thursday
TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def sub(data, seed_id):
    return next(s for s in data["submissions"] if s["seedId"] == seed_id)


def all_timestamps(data):
    for s in data["submissions"]:
        yield s["createdAt"]
        for v in s["versions"]:
            yield v["createdAt"]
        for key in ("decisions", "comments", "dismissals"):
            for item in s[key]:
                yield item["createdAt"]


def test_spot_checks(seed):
    r = resolve_times(seed, NOW)
    assert sub(r, 1)["launchDate"] == "2026-10-02"          # tomorrow
    assert sub(r, 11)["launchDate"] == "2026-09-30"         # yesterday
    assert sub(r, 12)["launchDate"] == "2026-10-31"         # +30
    assert sub(r, 5)["versions"][0]["createdAt"] == "2026-09-26T12:30:15Z"   # 120h before now
    assert sub(r, 5)["versions"][1]["createdAt"] == "2026-09-29T20:30:15Z"   # 40h before now
    assert sub(r, 3)["comments"][0]["createdAt"] == "2026-10-01T09:30:15Z"   # 3h before now


def test_offset_keys_are_replaced(seed):
    r = resolve_times(seed, NOW)
    text = repr(r)
    for gone in ("launchOffsetDays", "createdHoursAgo", "hoursAgo"):
        assert gone not in text


def test_everything_else_is_unchanged(seed):
    r = resolve_times(seed, NOW)
    for a, b in zip(seed["submissions"], r["submissions"]):
        for key in ("seedId", "title", "product", "channel", "status", "currentVersion", "submittedBy"):
            assert a[key] == b[key]
        assert [v["copy"] for v in a["versions"]] == [v["copy"] for v in b["versions"]]
        assert [d["reason"] for d in a["decisions"]] == [d["reason"] for d in b["decisions"]]


def test_input_is_not_mutated(seed):
    before = copy.deepcopy(seed)
    resolve_times(seed, NOW)
    assert seed == before


def test_output_formats_are_exact(seed):
    r = resolve_times(seed, NOW)
    for s in r["submissions"]:
        assert DATE.fullmatch(s["launchDate"])
    for t in all_timestamps(r):
        assert TS.fullmatch(t), t
        assert "+00:00" not in t and "." not in t


def test_no_timestamp_is_in_the_future(seed):
    r = resolve_times(seed, NOW)
    limit = format_timestamp(NOW)
    assert all(t <= limit for t in all_timestamps(r))


def test_event_order_is_preserved(seed):
    r = resolve_times(seed, NOW)
    for s in r["submissions"]:
        created = {v["versionNumber"]: v["createdAt"] for v in s["versions"]}
        assert s["createdAt"] >= min(created.values())
        assert [v["createdAt"] for v in s["versions"]] == sorted(v["createdAt"] for v in s["versions"])
        for key in ("decisions", "comments", "dismissals"):
            for item in s[key]:
                assert item["createdAt"] >= created[item["versionNumber"]]
        for d in s["decisions"]:
            nxt = created.get(d["versionNumber"] + 1)
            if nxt:
                assert d["createdAt"] < nxt  # resubmission follows the decision


def test_same_now_gives_identical_output(seed):
    assert resolve_times(seed, NOW) == resolve_times(seed, NOW)


def test_microseconds_do_not_change_output(seed):
    assert resolve_times(seed, NOW) == resolve_times(seed, NOW.replace(microsecond=999999))


def test_different_now_shifts_everything_by_the_same_delta(seed):
    later = NOW + timedelta(days=3, hours=5)
    a, b = resolve_times(seed, NOW), resolve_times(seed, later)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    for x, y in zip(all_timestamps(a), all_timestamps(b)):
        assert datetime.strptime(y, fmt) - datetime.strptime(x, fmt) == later.replace(microsecond=0) - NOW.replace(microsecond=0)
    for sa, sb in zip(a["submissions"], b["submissions"]):
        assert (datetime.fromisoformat(sb["launchDate"]) - datetime.fromisoformat(sa["launchDate"])).days == 3


@pytest.mark.parametrize("now,offset,expected", [
    (datetime(2026, 12, 31, 10, tzinfo=timezone.utc), 1, "2027-01-01"),
    (datetime(2026, 12, 31, 10, tzinfo=timezone.utc), 2, "2027-01-02"),
    (datetime(2028, 2, 28, 10, tzinfo=timezone.utc), 1, "2028-02-29"),   # leap day exists
    (datetime(2028, 2, 28, 10, tzinfo=timezone.utc), 2, "2028-03-01"),
    (datetime(2027, 2, 28, 10, tzinfo=timezone.utc), 1, "2027-03-01"),   # and not in 2027
    (datetime(2026, 1, 1, 10, tzinfo=timezone.utc), -1, "2025-12-31"),
    (datetime(2026, 3, 1, 10, tzinfo=timezone.utc), -1, "2026-02-28"),
    (datetime(2026, 10, 1, 10, tzinfo=timezone.utc), 0, "2026-10-01"),
])
def test_date_rollovers(seed, now, offset, expected):
    data = {"submissions": [copy.deepcopy(sub(seed, 1))]}
    data["submissions"][0]["launchOffsetDays"] = offset
    assert resolve_times(data, now)["submissions"][0]["launchDate"] == expected


def test_hours_cross_midnight_and_year(seed):
    now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
    r = resolve_times(seed, now)
    assert sub(r, 3)["comments"][0]["createdAt"] == "2025-12-31T22:00:00Z"  # 3h ago


def test_fractional_hours_round_to_the_second(seed):
    data = {"submissions": [copy.deepcopy(sub(seed, 1))]}
    data["submissions"][0]["createdHoursAgo"] = 0.5
    r = resolve_times(data, datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert r["submissions"][0]["createdAt"] == "2026-10-01T11:30:00Z"


def test_zero_hours_ago_is_now_and_allowed(seed):
    data = {"submissions": [copy.deepcopy(sub(seed, 1))]}
    data["submissions"][0]["createdHoursAgo"] = 0
    data["submissions"][0]["versions"][0]["createdHoursAgo"] = 0
    assert resolve_times(data, NOW)["submissions"][0]["createdAt"] == "2026-10-01T12:30:15Z"


def test_non_utc_aware_now_is_converted_to_utc(seed):
    plus9 = timezone(timedelta(hours=9))
    local = datetime(2026, 10, 2, 7, 30, 15, tzinfo=plus9)   # 22:30:15 UTC on Oct 1
    assert resolve_times(seed, local) == resolve_times(seed, datetime(2026, 10, 1, 22, 30, 15, tzinfo=timezone.utc))
    # Date comes from the UTC day, not the local one.
    assert sub(resolve_times(seed, local), 1)["launchDate"] == "2026-10-02"


@pytest.mark.parametrize("bad", [datetime(2026, 10, 1, 12), None, "2026-10-01", 5])
def test_now_must_be_timezone_aware(seed, bad):
    with pytest.raises(ValueError):
        resolve_times(seed, bad)


def test_timestamps_sort_as_text_in_time_order(seed):
    r = resolve_times(seed, NOW)
    s5 = sub(r, 5)
    parsed = [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") for t in (v["createdAt"] for v in s5["versions"])]
    assert parsed == sorted(parsed)
    assert [v["createdAt"] for v in s5["versions"]] == sorted(v["createdAt"] for v in s5["versions"])
