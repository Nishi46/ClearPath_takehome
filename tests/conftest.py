import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("CLEARPATH_DB", str(path))
    return path


@pytest.fixture
def client(db_path):
    """A client that has run app startup, so the schema exists and the demo seed is loaded."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def conn(db_path):
    """A connection to a temp DB with the schema applied; committed and closed on exit."""
    from pathlib import Path

    from app import db

    schema = (Path(db.__file__).parent / "schema.sql").read_text()
    with db.connect() as connection:
        connection.executescript(schema)
        yield connection


@pytest.fixture(autouse=True)
def _fresh_reset_cooldown():
    """The reset cooldown is process-wide state; start every test with it clear."""
    from app.routes import pages

    pages.reset_cooldown.clear()
    yield
    pages.reset_cooldown.clear()


@pytest.fixture
def live_client(db_path):
    """A client that ran app startup, so the demo seed is loaded."""
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def mclient(client):
    """The seeded client acting as a marketer (the default marketer is Maya Chen)."""
    client.cookies.set("role", "marketer")
    return client


@pytest.fixture
def aclient(client):
    """The seeded client acting as an affiliate partner (the default partner is Northwind Referrals)."""
    client.cookies.set("role", "affiliate")
    return client


# ---- a pinned clock for date edge cases ----

# A real week, Monday to Sunday, so every name maps to one fixed UTC date.
WEEK = {"monday": "2026-10-05", "tuesday": "2026-10-06", "wednesday": "2026-10-07", "thursday": "2026-10-08",
        "friday": "2026-10-09", "saturday": "2026-10-10", "sunday": "2026-10-11"}


def pin_clock(monkeypatch, name, hour=9, minute=0):
    """Make clock.now() return the given weekday at hour:minute UTC. Undone by the monkeypatch."""
    from datetime import datetime, timezone

    from app import clock

    day = WEEK[name.lower()]
    moment = datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: moment)
    return moment


@pytest.fixture(autouse=True)
def _marked_weekday(request, monkeypatch):
    """`@pytest.mark.weekday("friday")` pins the clock before any other fixture runs.

    Autouse fixtures are set up first, so `client` seeds the demo on the pinned day.
    """
    marker = request.node.get_closest_marker("weekday")
    if marker:
        pin_clock(monkeypatch, *marker.args, **marker.kwargs)


@pytest.fixture
def at_weekday(monkeypatch):
    """Call `at_weekday("wednesday")` inside a test to move the clock (for example before a reset)."""
    return lambda name, hour=9, minute=0: pin_clock(monkeypatch, name, hour, minute)
