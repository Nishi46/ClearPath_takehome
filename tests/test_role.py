import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.roles import ROLES, get_role

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def current_role(html):
    m = re.search(r'value="(\w+)"\s+class="role-option"\s+aria-current="true"', html)
    return m.group(1) if m else None


def get_with_cookie(client, value):
    return client.get("/", headers={"Cookie": "role=%s" % value})


def test_roles_defined_in_one_place():
    assert ROLES == ("reviewer", "marketer")


def test_no_cookie_defaults_to_reviewer(client):
    r = client.get("/")
    assert r.status_code == 200
    assert current_role(r.text) == "reviewer"
    assert "Reviewer (current)" in r.text and "Marketer (current)" not in r.text


def test_valid_marketer_cookie_renders_marketer(client):
    r = get_with_cookie(client, "marketer")
    assert current_role(r.text) == "marketer"
    assert "Reviewer (current)" not in r.text
    assert "Marketer (current)" in r.text


@pytest.mark.parametrize("bad", [
    "admin", "", "a" * 5000, "<script>", "reviewer; x", "MARKETER",
    "reviewer,marketer", "../etc/passwd", "%3Cscript%3E", "mark eter",
])
def test_tampered_cookie_falls_back_to_reviewer(client, bad):
    r = get_with_cookie(client, bad)
    assert r.status_code == 200
    assert current_role(r.text) == "reviewer"
    assert "&lt;script" not in r.text and "<script>" not in r.text
    assert "admin" not in r.text and "aaaaaaaaaa" not in r.text


def test_switching_to_reviewer_lands_on_the_queue(client):
    r = client.post("/role", data={"role": "reviewer"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_post_role_sets_cookie_and_redirects(client):
    r = client.post("/role", data={"role": "marketer"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/mine"        # a marketer lands on their own submissions
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("role=marketer")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=2592000" in cookie
    assert "secure" not in cookie.replace("samesite", "")  # plain http locally


def test_cookie_is_secure_over_https(db_path):
    https = TestClient(app, base_url="https://testserver")
    r = https.post("/role", data={"role": "marketer"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower().replace("samesite", "")


def test_cookie_is_secure_behind_tls_proxy(client):
    r = client.post("/role", data={"role": "reviewer"}, follow_redirects=False,
                    headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower().replace("samesite", "")


@pytest.mark.parametrize("data", [
    {}, {"role": ""}, {"role": "admin"}, {"role": "Marketer"}, {"role": " marketer"},
    {"role": "<script>alert(1)</script>"}, {"other": "marketer"}, {"role": "a" * 5000},
])
def test_post_role_rejects_bad_values_and_sets_no_cookie(client, data):
    r = client.post("/role", data=data, follow_redirects=False)
    assert r.status_code == 400
    assert "set-cookie" not in r.headers
    assert "alert(1)" not in r.text and "admin" not in r.text and "Unknown role." in r.text


def test_post_role_with_json_body_is_rejected(client):
    r = client.post("/role", json={"role": "marketer"}, follow_redirects=False)
    assert r.status_code == 400
    assert "set-cookie" not in r.headers


def test_get_role_endpoint_is_405(client):
    r = client.get("/role", params={"role": "marketer"})
    assert r.status_code == 405
    assert "set-cookie" not in r.headers


def test_other_methods_are_405(client):
    for method in ("put", "delete", "patch"):
        assert client.request(method.upper(), "/role", data={"role": "marketer"}).status_code == 405


def test_switching_persists_across_requests(client):
    assert current_role(client.get("/").text) == "reviewer"
    r = client.post("/role", data={"role": "marketer"})  # follows the 303
    assert current_role(r.text) == "marketer"
    assert current_role(client.get("/").text) == "marketer"
    client.post("/role", data={"role": "reviewer"})
    assert current_role(client.get("/").text) == "reviewer"


def test_both_roles_offered_as_post_buttons(client):
    html = client.get("/").text
    assert re.search(r'<form method="post" action="/role"', html)
    for role in ROLES:
        assert 'name="role" value="%s"' % role in html


def test_role_text_not_only_color(client):
    html = client.get("/").text
    assert "(current)" in html and "Role:" in html


def test_get_role_unit():
    class Req:
        def __init__(self, cookies):
            self.cookies = cookies

    assert get_role(Req({})) == "reviewer"
    assert get_role(Req({"role": "marketer"})) == "marketer"
    assert get_role(Req({"role": "root"})) == "reviewer"


def test_role_cookie_is_not_used_for_authorization():
    """The cookie is a demo label (assumption A4). Only roles.py may read it, and only the
    template helper, the review decision route and the submit routes may call get_role. Their use is
    a product guard (a marketer should not approve their own copy, a reviewer does not submit), not
    authorization. The marketer identity works the same way: only roles.py reads its cookie."""
    roles_py = APP_DIR / "roles.py"
    assert "demo label" in roles_py.read_text()
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text()
        if path != roles_py:
            assert "request.cookies" not in text, path
        if path.name not in ("roles.py", "templating.py", "pages.py", "submit_pages.py"):
            assert "get_role" not in text, path
        if path.name == "pages.py":
            # The decision, dismiss and comment routes (product guards) and the review page, which only
            # picks the default back link for a marketer and whether to prefill a snippet. None of
            # them grants or denies access to data.
            assert text.count("get_role(") == 5, path
        if path.name == "submit_pages.py":
            # POST /submit, POST /submit/check, the resubmit page and POST /resubmit/{id}
            assert text.count("get_role(") == 4, path
