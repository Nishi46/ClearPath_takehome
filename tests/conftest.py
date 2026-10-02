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
