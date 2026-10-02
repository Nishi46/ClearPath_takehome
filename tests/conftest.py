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
