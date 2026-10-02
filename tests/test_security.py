import http.client
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import CSP, MAX_BODY_BYTES

REPO = Path(__file__).resolve().parent.parent
REQUIRED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
}


def assert_secure(response):
    for name, value in REQUIRED.items():
        assert response.headers.get(name) == value, name
    assert response.headers.get("content-security-policy") == CSP


@pytest.fixture
def boom_client(db_path):
    """A client with a test-only route that raises, and no re-raise of server errors."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/__boom")
    def boom():
        raise RuntimeError("secret-internal-detail at /Users/someone/app/db.py")

    app.include_router(router)
    added = app.router.routes[-1]
    yield TestClient(app, raise_server_exceptions=False)
    app.router.routes.remove(added)


# headers

@pytest.mark.parametrize("method,path,status", [
    ("get", "/", 200),
    ("get", "/healthz", 200),
    ("get", "/static/style.css", 200),
    ("get", "/static/htmx.min.js", 200),
    ("get", "/no-such-page", 404),
    ("get", "/static/nope.css", 404),
    ("post", "/healthz", 405),
    ("get", "/role", 405),
])
def test_every_response_carries_security_headers(client, method, path, status):
    r = getattr(client, method)(path)
    assert r.status_code == status
    assert_secure(r)


def test_headers_on_redirect_and_validation_error(client):
    assert_secure(client.post("/role", data={"role": "marketer"}, follow_redirects=False))
    assert_secure(client.post("/role", data={"role": "nope"}))


def test_csp_is_strict():
    assert "unsafe-eval" not in CSP
    directives = {d.split()[0]: d.split()[1:] for d in CSP.split("; ")}
    assert directives["script-src"] == ["'self'"]
    assert directives["style-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["object-src"] == ["'none'"]
    assert "*" not in CSP
    assert "unsafe-inline" not in CSP


def test_htmx_is_configured_not_to_need_inline_style_or_eval(client):
    html = client.get("/").text
    assert '"includeIndicatorStyles": false' in html
    assert '"allowEval": false' in html


# error pages

def test_404_is_friendly_html_with_link_to_queue(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "Page not found" in r.text
    assert 'href="/"' in r.text and "Back to the queue" in r.text
    assert "Traceback" not in r.text and "detail" not in r.text


def test_500_is_friendly_and_leaks_nothing(boom_client):
    r = boom_client.get("/__boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("text/html")
    assert "We hit a problem" in r.text
    assert "Back to the queue" in r.text
    for leak in ("Traceback", "RuntimeError", "secret-internal-detail", "/Users/", ".py",
                 "File \"", "site-packages"):
        assert leak not in r.text, leak
    assert_secure(r)


def test_500_falls_back_if_template_breaks(boom_client, monkeypatch):
    import app.errors as errors

    def broken(*args, **kwargs):
        raise RuntimeError("template exploded")

    monkeypatch.setattr(errors, "render", broken)
    r = boom_client.get("/__boom")
    assert r.status_code == 500
    assert "We hit a problem" in r.text and "template exploded" not in r.text
    assert_secure(r)


def test_app_keeps_working_after_a_500(boom_client):
    boom_client.get("/__boom")
    assert boom_client.get("/healthz").status_code == 200


def test_debug_mode_is_off():
    assert app.debug is False


def test_deploy_config_never_uses_reload():
    for name in ("render.yaml", "Procfile", "start.sh"):
        path = REPO / name
        if path.exists():
            assert "--reload" not in path.read_text(), name


# oversized input

def test_10mb_body_to_role_is_rejected(client):
    r = client.post("/role", content=b"role=" + b"a" * (10 * 1024 * 1024),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert "set-cookie" not in r.headers
    assert_secure(r)
    assert client.get("/healthz").status_code == 200


def test_chunked_body_without_content_length_is_limited(client):
    def chunks():
        for _ in range(12):
            yield b"role=" + b"a" * (100 * 1024)

    r = client.post("/role", content=chunks(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert client.get("/healthz").status_code == 200


def test_body_at_the_limit_is_still_processed(client):
    body = b"role=" + b"a" * 1000
    assert len(body) < MAX_BODY_BYTES
    assert client.post("/role", content=body,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 400


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_oversized_header_is_refused_by_real_server_and_app_survives(tmp_path):
    port = free_port()
    env = dict(os.environ, CLEARPATH_DB=str(tmp_path / "x.db"), PYTHONPATH=str(REPO))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=str(REPO), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/", headers={"X-Big": "a" * (300 * 1024)})
            status = conn.getresponse().status
        except (ConnectionError, http.client.HTTPException, OSError):
            status = None  # connection dropped before a response: also a refusal
        finally:
            conn.close()
        assert status in (None, 400, 431)

        ok = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        ok.request("GET", "/healthz")
        resp = ok.getresponse()
        assert resp.status == 200 and resp.read() == b'{"status":"ok"}'
        assert resp.getheader("X-Frame-Options") == "DENY"
        ok.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
