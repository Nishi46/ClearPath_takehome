import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_healthy_returns_exact_body(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.content == b'{"status":"ok"}'
    assert r.headers["content-type"] == "application/json"


def test_healthy_after_startup_with_schema(db_path):
    with TestClient(app) as client:
        assert client.get("/healthz").content == b'{"status":"ok"}'


def test_missing_folder_returns_503_without_leaking(tmp_path, monkeypatch, caplog):
    bad = tmp_path / "no-such-folder" / "secret-name.db"
    monkeypatch.setenv("CLEARPATH_DB", str(bad))
    with caplog.at_level(logging.ERROR):
        r = TestClient(app).get("/healthz")
    assert r.status_code == 503
    assert r.content == b'{"status":"unavailable"}'
    for leak in ("no-such-folder", "secret-name", str(tmp_path), "Error", "Traceback", "sqlite"):
        assert leak not in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "no-such-folder" in caplog.text  # the detail is logged server-side instead


def test_corrupt_database_returns_503_without_leaking(tmp_path, monkeypatch):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite file" * 100)
    monkeypatch.setenv("CLEARPATH_DB", str(bad))
    r = TestClient(app).get("/healthz")
    assert r.status_code == 503
    assert r.content == b'{"status":"unavailable"}'
    assert "not a database" not in r.text and "corrupt" not in r.text


def test_post_is_405(client):
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/healthz").status_code == 405


def test_cache_control_no_store(client):
    assert client.get("/healthz").headers["cache-control"] == "no-store"


def test_response_has_no_version_or_server_details(client):
    r = client.get("/healthz")
    assert r.json() == {"status": "ok"}
    assert str(Path.cwd()) not in r.text
