from datetime import date, datetime, timedelta, timezone

# Everything that needs "now" or "today" calls these, so tests patch one place.
# Dates are UTC dates: simple and DST-free, at the cost of being off by a few hours for local users.

OPEN_STATUSES = ("new", "in_review", "changes_requested")
FINAL_STATUSES = ("approved", "rejected")
RUSH_BUSINESS_DAYS = 2


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return now().date()


def _as_date(value, name):
    # datetime is a date subclass; accepting it would silently compare a time against a day.
    if isinstance(value, datetime):
        raise ValueError(f"{name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be a date or a YYYY-MM-DD string")


def business_days_until(today, launch) -> int:
    """Monday-to-Friday days after `today` up to and including `launch`; no holidays.

    Zero when launch is today, in the past, or only weekend days away.
    Uses arithmetic, not a day-by-day loop, so a far-future date costs nothing.
    """
    today = _as_date(today, "today")
    launch = _as_date(launch, "launch")
    days = (launch - today).days
    if days <= 0:
        return 0
    count = (days // 7) * 5
    for i in range(1, days % 7 + 1):
        if (today + timedelta(days=i)).weekday() < 5:
            count += 1
    return count


def urgency(today, launch, status):
    """'overdue', 'rush' or None. Only open items are ever urgent."""
    if status in FINAL_STATUSES:
        return None
    if status not in OPEN_STATUSES:
        raise ValueError("unknown status")
    today = _as_date(today, "today")
    launch = _as_date(launch, "launch")
    if launch < today:
        return "overdue"
    if business_days_until(today, launch) <= RUSH_BUSINESS_DAYS:
        return "rush"
    return None
