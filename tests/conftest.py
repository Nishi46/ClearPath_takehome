import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("CLEARPATH_DB", str(path))
    return path


@pytest.fixture
def client(db_path):
    from app.main import app

    return TestClient(app)


@pytest.fixture
def conn(db_path):
    """A connection to a temp DB with the schema applied; committed and closed on exit."""
    from pathlib import Path

    from app import db

    schema = (Path(db.__file__).parent / "schema.sql").read_text()
    with db.connect() as connection:
        connection.executescript(schema)
        yield connection
