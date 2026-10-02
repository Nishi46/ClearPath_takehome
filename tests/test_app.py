import importlib
import sys


def test_import_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", str(tmp_path / "test.db"))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app.main", None)
    importlib.import_module("app.main")
    assert list(tmp_path.iterdir()) == []


def test_docs_endpoints_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_unknown_path_is_404_without_stack_trace(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert "Traceback" not in r.text
    assert ".py" not in r.text
