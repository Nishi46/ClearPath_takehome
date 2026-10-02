"""Phase 7 steps 22 to 24: route table and headers, cookies and role switching, hostile input across the whole app."""
import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import clock, db
from app.main import app
from app.routes import pages
from app.security import CSP
from tests.test_phase7_edge_matrix import as_role, counts
from tests.test_phase7_polish import SCREENS, render

HEADERS = {"origin": "http://testserver"}
EVIL = {"origin": "https://evil.example"}
ROOT = Path(__file__).resolve().parent.parent


def route_set():
    routes = []
    for r in app.routes:
        routes += r.original_router.routes if hasattr(r, "original_router") else [r]
    return {(r.path, tuple(sorted(r.methods - {"HEAD"}))) for r in routes if hasattr(r, "methods") and r.path != "/static"}


# ---- step 22: route table and headers ----

PHASE_6_ROUTES = {
    ("/", ("GET",)), ("/healthz", ("GET",)), ("/role", ("POST",)), ("/marketer", ("POST",)),
    ("/affiliate", ("POST",)),
    ("/reset/confirm", ("GET",)), ("/reset", ("POST",)), ("/mine", ("GET",)),
    ("/submit", ("GET",)), ("/submit", ("POST",)), ("/submit/check", ("POST",)),
    ("/resubmit/{submission_id}", ("GET",)), ("/resubmit/{submission_id}", ("POST",)),
    ("/review/{submission_id}", ("GET",)), ("/review/{submission_id}/decision", ("POST",)),
    ("/review/{submission_id}/dismiss", ("POST",)), ("/review/{submission_id}/comment", ("POST",)),
    # Phase 8: import from Excel (a marketer or partner product guard, like /submit).
    ("/import", ("GET",)), ("/import/preview", ("POST",)), ("/import/confirm", ("POST",)),
    ("/import/sample.xlsx", ("GET",)),
}


def test_the_route_table_is_exactly_the_phase_6_surface():
    """Polish added nothing by accident. A new route must be added here on purpose, with a review."""
    assert route_set() == PHASE_6_ROUTES


def test_no_docs_or_debug_routes_exist(client):
    for path in ("/docs", "/redoc", "/openapi.json", "/__boom", "/admin", "/debug", "/.env", "/.git/config", "/static/../app/main.py",
                 "/static/%2e%2e/app/main.py", "/static/..%2fapp%2fdb.py"):
        assert client.get(path).status_code in (404, 400), path


REQUIRED = {"x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "same-origin"}


def assert_headers(r):
    for name, value in REQUIRED.items():
        assert r.headers.get(name) == value, name
    assert r.headers.get("content-security-policy") == CSP
    assert "frame-ancestors 'none'" in CSP and "form-action 'self'" in CSP and "object-src 'none'" in CSP


@pytest.mark.parametrize("role,who,path", SCREENS)
def test_every_screen_carries_the_security_headers_and_is_never_cached(client, role, who, path):
    as_role(client, role, who)
    r = client.get(path)
    assert_headers(r)
    if r.status_code == 200 and not path.startswith("/healthz"):
        assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/static/style.css", "/static/htmx.min.js", "/static/queue.js", "/static/favicon.svg"])
def test_static_files_carry_the_security_headers_too(client, path):
    assert_headers(client.get(path))


def test_error_responses_of_every_kind_carry_the_headers(client):
    as_role(client, "marketer", "Maya Chen")
    for r in (client.get("/nowhere"), client.put("/submit"), client.post("/review/3/decision", data={}, headers=HEADERS),
              client.post("/submit", data={}, headers=EVIL),
              client.post("/submit", content=b"x=" + b"a" * (1024 * 1024 + 5),
                          headers={**HEADERS, "content-type": "application/x-www-form-urlencoded"})):
        assert_headers(r)


def test_the_review_page_cannot_be_framed(client):
    r = client.get("/review/3")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    post = client.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS, follow_redirects=False)
    assert post.headers["x-frame-options"] == "DENY"


def test_the_server_is_started_without_a_version_banner():
    assert "--no-server-header" in (ROOT / "render.yaml").read_text()


def test_hsts_is_sent_only_over_https(client):
    assert "strict-transport-security" not in client.get("/").headers
    secure = client.get("/", headers={"x-forwarded-proto": "https"})
    assert secure.headers["strict-transport-security"].startswith("max-age=")


POST_ROUTES = [("/role", {"role": "marketer"}), ("/marketer", {"name": "Maya Chen"}), ("/reset", {"confirm": "reset"}),
               ("/submit", {"title": "x"}), ("/submit/check", {"product": "loan"}), ("/resubmit/14", {"copy": "x"}),
               ("/review/3/decision", {"outcome": "approved", "version": "1"}),
               ("/review/12/dismiss", {"rule_id": "R4", "version": "1", "note": "n"}),
               ("/review/3/comment", {"text": "t", "version": "1"})]


@pytest.mark.parametrize("path,data", POST_ROUTES)
def test_state_changing_routes_refuse_get(client, path, data):
    if path in ("/submit", "/resubmit/14"):
        pytest.skip("this path also has a read-only GET page; a GET never writes (checked below)")
    before = counts()
    r = client.get(path + "?" + "&".join("%s=%s" % kv for kv in data.items()))
    assert r.status_code in (404, 405), path
    assert counts() == before


def test_a_get_never_writes_even_with_every_field_in_the_query(client):
    as_role(client, "marketer", "Jordan Lee")
    before = counts()
    for path, data in POST_ROUTES:
        client.get(path + "?" + "&".join("%s=%s" % kv for kv in data.items()))
    assert counts() == before


@pytest.mark.parametrize("headers", [EVIL, {"origin": "null"}, {"referer": "https://evil.example/x"},
                                     {"origin": "http://testserver.evil.example"}, {"origin": "http://testserver:99999"}])
@pytest.mark.parametrize("path,data", POST_ROUTES)
def test_state_changing_routes_refuse_cross_origin_posts_and_write_nothing(client, path, data, headers):
    for who in ("reviewer", "marketer"):
        as_role(client, who, "Jordan Lee")
        before = counts()
        cookies_before = dict(client.cookies)
        r = client.post(path, data=data, headers=headers, follow_redirects=False)
        assert r.status_code == 403, (path, headers)
        assert counts() == before and "set-cookie" not in r.headers


@pytest.mark.parametrize("path,data", POST_ROUTES)
def test_state_changing_routes_survive_an_empty_post(client, path, data):
    pages.reset_cooldown.clear()
    for who in ("reviewer", "marketer"):
        r = as_role(client, who, "Maya Chen").post(path, data={}, headers=HEADERS, follow_redirects=False)
        assert r.status_code < 500


# ---- step 23: cookies and role switching ----

def cookie_header(response, name):
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(name + "="))


@pytest.mark.parametrize("path,data,name", [("/role", {"role": "marketer"}, "role"), ("/marketer", {"name": "Jordan Lee"}, "marketer")])
def test_cookie_attributes(client, path, data, name):
    r = client.post(path, data=data, headers=HEADERS, follow_redirects=False)
    c = cookie_header(r, name).lower()
    assert "httponly" in c and "samesite=lax" in c and "path=/" in c and "max-age=" in c and "secure" not in c.replace("samesite", "")
    over_tls = client.post(path, data=data, headers={**HEADERS, "x-forwarded-proto": "https"}, follow_redirects=False)
    assert "secure" in cookie_header(over_tls, name).lower().replace("samesite", "")


@pytest.mark.parametrize("value", ["admin", "", "Reviewer", "REVIEWER", "marketers", "market", "reviewer2", "a" * 5000,
                                   "\u202Ereviewer", "réviewer", "__proto__", "<script>alert(1)</script>", "../../etc/passwd"])
def test_a_tampered_role_cookie_falls_back_and_is_never_echoed(client, value):
    r = client.get("/", headers={"cookie": b"role=" + value.encode("utf-8")})
    assert r.status_code == 200 and (len(value) < 12 or value not in r.text)
    assert "Reviewer (current)" in r.text                                # the documented default
    assert client.get("/submit", headers={"cookie": b"role=" + value.encode("utf-8")}).status_code == 200


@pytest.mark.parametrize("value", ["Mallory", "maya chen", "Maya  Chen", "", "Maya Chen\r\nX-Injected: 1", "x" * 5000, "<b>", "Sam Patel2"])
def test_a_tampered_marketer_cookie_falls_back_to_maya(client, value):
    if "\r" in value:
        pytest.skip("a raw CR/LF cannot be sent in a cookie header at all")
    r = client.get("/mine", headers={"cookie": b"role=marketer; marketer=" + value.encode("utf-8")})
    assert r.status_code == 200 and "Maya Chen (current)" in r.text
    assert len(value) < 12 or value not in r.text


def test_a_tampered_marketer_cannot_become_the_submitter(client):
    as_role(client, "marketer")
    client.cookies.set("marketer", "Mallory'); DROP TABLE submission; --")
    r = client.post("/submit", headers=HEADERS, follow_redirects=False, data={
        "title": "Tamper", "product": "loan", "channel": "email", "notes": "",
        "copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
    with db.connect() as c:
        assert c.execute("SELECT submitted_by FROM submission WHERE title = 'Tamper'").fetchone()[0] == "Maya Chen"
    assert r.status_code == 303


@pytest.mark.parametrize("value", ["//evil.com", "https://evil.com", "/\\evil.com", "%2F%2Fevil.com", "javascript:alert(1)",
                                   "\\\\evil.com", "/%09/evil.com", "http:evil.com", "/?x=1\r\nLocation: https://evil.com"])
@pytest.mark.parametrize("field", ["next", "redirect", "url", "return", "back", "to", "r"])
def test_role_and_marketer_switches_redirect_only_to_fixed_paths(client, field, value):
    r = client.post("/role", data={"role": "marketer", field: value}, headers=HEADERS, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mine"
    r = client.post("/role", data={"role": "reviewer", field: value}, headers=HEADERS, follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.post("/marketer", data={"name": "Maya Chen", field: value}, headers=HEADERS, follow_redirects=False)
    assert r.headers["location"] == "/mine"


@pytest.mark.parametrize("back", ["//evil.com", "https://evil.com", "/\\evil.com", "%2F%2Fevil.com", "/?status=new&back=//evil.com",
                                  "/?status=%3Cscript%3E", "javascript:alert(1)", "/mine/../../x", "/?status=new\r\nX-Injected: 1"])
def test_a_hostile_back_value_is_rebuilt_or_dropped(client, back):
    page = client.get("/review/3?back=" + quote(back, safe="")).text
    assert "evil.com" not in page and "javascript:" not in page and "X-Injected" not in page
    r = client.post("/review/3/decision", data={"outcome": "approved", "version": "1", "back": back}, headers=HEADERS,
                    follow_redirects=False)
    assert r.status_code == 303 and "evil" not in r.headers["location"] and "x-injected" not in r.headers


def test_switching_role_changes_no_data_and_a_cross_origin_page_cannot_flip_it(client):
    before = counts()
    client.post("/role", data={"role": "marketer"}, headers=HEADERS)
    assert counts() == before
    as_role(client, "reviewer")
    r = client.post("/role", data={"role": "marketer"}, headers=EVIL, follow_redirects=False)
    assert r.status_code == 403 and "set-cookie" not in r.headers
    assert "Reviewer (current)" in client.get("/").text


def test_role_cookie_alone_cannot_get_around_a_server_check(client):
    # The role is a label (assumption A4), but the product guards still hold: a marketer cookie cannot decide.
    as_role(client, "marketer", "Maya Chen")
    before = counts()
    assert client.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS).status_code == 403
    assert counts() == before


# ---- step 24: hostile input across the whole app ----

CORPUS = [
    "' OR 1=1 --", "x'; DROP TABLE submission; --", "\" OR \"\"=\"", "{{1337*7331}}", "{% for i in range(9) %}{{i}}{% endfor %}",
    "${7*7}", "#{7*7}", "<script>alert(1)</script>", "\"><svg onload=alert(1)>", "' onfocus=alert(1) autofocus x='",
    "<img src=x onerror=alert(1)>", "javascript:alert(1)", "\x00", "a\x00b", "line1\r\nX-Injected: yes", "\r\nSet-Cookie: pwn=1",
    "café", "é", "\u202Egnirts", "\u2066x\u2069", "​‍﻿", "аdmin", "\U0001F600" * 50, "A" * 20_000,
    "../../../etc/passwd", "%00", "%0d%0aX-Injected: yes", "\\", "'", "\"", "<", ">", "&", "&amp;", "&#x3c;script&#x3e;",
    "9" * 400, "-1", "1e999", "NaN", "null", "true", "[]", "{}",
]
FORMS = [
    ("/submit", ["title", "product", "channel", "launch_date", "copy", "notes"], "marketer"),
    ("/submit/check", ["title", "product", "channel", "launch_date", "copy", "notes"], "marketer"),
    ("/resubmit/14", ["copy", "notes", "launch_date", "base_version", "action"], "marketer:Jordan Lee"),
    ("/review/3/decision", ["outcome", "version", "reason", "back"], "reviewer"),
    ("/review/12/dismiss", ["rule_id", "version", "note", "back"], "reviewer"),
    ("/review/3/comment", ["text", "version", "rule_id", "back"], "reviewer"),
    ("/role", ["role"], "reviewer"), ("/marketer", ["name"], "marketer"), ("/reset", ["confirm"], "reviewer"),
]
QUERIES = [("/", ["status", "product", "channel"]), ("/mine", ["submitted"]),
           ("/review/3", ["v", "diff", "snippet", "back"]), ("/review/12", ["v", "diff", "snippet", "back"])]
BASE_GOOD = {"title": "Fuzz", "product": "loan", "channel": "email", "launch_date": "2099-01-01", "copy": "c", "notes": "n",
             "base_version": "1", "outcome": "approved", "version": "1", "reason": "r", "rule_id": "R4", "note": "n", "text": "t",
             "role": "reviewer", "name": "Maya Chen", "confirm": "reset", "back": "/", "action": ""}


class Attrs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.bad = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name.lower().startswith("on") or name.lower() == "srcdoc":
                self.bad.append((tag, name))
            if name.lower() == "formaction" and value not in ("/submit/check",):   # the one built-in button override
                self.bad.append((tag, name, value))
        if tag == "script" and not dict(attrs).get("src"):
            self.bad.append((tag, "inline"))


def assert_inert(response, payload):
    text = response.text
    assert response.status_code < 500, (response.status_code, payload[:40])
    assert "9801347" not in text and "49</" not in text.replace("<td", "") or "{{" not in payload
    assert "x-injected" not in {k.lower() for k in response.headers} and "pwn" not in response.headers.get("set-cookie", "")
    assert "\r" not in "".join(response.headers.values()) and "\n" not in "".join(response.headers.values())
    p = Attrs()
    p.feed(text)
    assert p.bad == [], (p.bad, payload[:40])


def logged_in(client, who):
    role, _, name = who.partition(":")
    return as_role(client, role, name or "Maya Chen")


@pytest.mark.parametrize("path,fields,who", FORMS)
def test_every_form_field_survives_the_hostile_corpus(client, path, fields, who):
    for field in fields:
        for payload in CORPUS:
            pages.reset_cooldown.clear()
            data = {f: BASE_GOOD[f] for f in fields}
            data[field] = payload
            before = counts()
            r = logged_in(client, who).post(path, data=data, headers=HEADERS, follow_redirects=False)
            assert_inert(r, payload)
            if r.status_code >= 400:
                assert counts() == before, (path, field, payload[:30], r.status_code)
            if path == "/reset":
                pages.reset_cooldown.clear()


@pytest.mark.parametrize("path,names", QUERIES)
def test_every_query_parameter_survives_the_hostile_corpus(client, path, names):
    for name in names:
        for payload in CORPUS:
            r = client.get(path + "?%s=%s" % (name, quote(payload, safe="")))
            assert_inert(r, payload)
            assert r.status_code in (200, 303, 404), (path, name, payload[:30], r.status_code)


def test_template_syntax_is_shown_as_text_never_run(client):
    as_role(client, "marketer", "Maya Chen")
    r = client.post("/submit", headers=HEADERS, follow_redirects=True, data={
        "title": "{{1337*7331}} {% raw %}", "product": "loan", "channel": "email", "notes": "{{config}}",
        "copy": "Apply today. {{7*7}} Rates from 5.99% APR. Subject to credit approval.",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
    assert "9801347" not in r.text and "{{1337*7331}}" in r.text
    page = as_role(client, "reviewer").get("/review/15").text
    assert "{{7*7}}" in page and "49 Rates" not in page and "{{config}}" in page


def test_a_hostile_cookie_and_header_mix_never_reaches_a_500(client):
    for value in CORPUS:
        client.cookies.clear()
        if "\r" in value or "\n" in value:
            continue
        r = client.get("/", headers={"cookie": b"role=" + value.encode("utf-8") + b"; marketer=" + value.encode("utf-8")})
        assert r.status_code < 500


# Direction-override and isolate characters can make text read differently from how it is stored: a reviewer
# could approve what they cannot see. They are refused in everything a person types.
BIDI = ["\u202A", "\u202B", "\u202C", "\u202D", "\u202E", "\u2066", "\u2067", "\u2068", "\u2069"]


@pytest.mark.parametrize("char", BIDI)
def test_text_direction_controls_are_refused_in_every_free_text_field(client, char):
    before = counts()
    as_role(client, "marketer", "Maya Chen")
    for field in ("title", "copy", "notes"):
        data = {"title": "Bidi", "product": "loan", "channel": "email", "notes": "n", "copy": "Apply today.",
                "launch_date": (clock.today() + timedelta(days=30)).isoformat()}
        data[field] = "start %sevil%s end" % (char, char)
        r = client.post("/submit", data=data, headers=HEADERS, follow_redirects=False)
        assert r.status_code == 422 and ("error-%s" % field) in r.text, (field, hex(ord(char)))
    as_role(client, "reviewer")
    for path, data in (("/review/3/decision", {"outcome": "rejected", "version": "1", "reason": "no %s yes" % char}),
                       ("/review/12/dismiss", {"rule_id": "R4", "version": "1", "note": "no %s yes" % char}),
                       ("/review/3/comment", {"text": "no %s yes" % char, "version": "1"})):
        r = client.post(path, data=data, headers=HEADERS, follow_redirects=False)
        assert r.status_code == 422, (path, hex(ord(char)))
    assert counts() == before


def test_ordinary_right_to_left_and_mixed_scripts_are_still_welcome(client):
    as_role(client, "marketer", "Maya Chen")
    r = client.post("/submit", headers=HEADERS, follow_redirects=False, data={
        "title": "שלום world مرحبا", "product": "loan", "channel": "email", "notes": "",
        "copy": "שלום. Apply today. مرحبا 5.99% APR.",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
    assert r.status_code == 303


def test_reasons_with_control_characters_are_refused_too(client):
    before = counts()
    for bad in ("no\x00pe", "bell\x07", "esc\x1b[31m"):
        r = client.post("/review/3/decision", data={"outcome": "rejected", "version": "1", "reason": bad},
                        headers=HEADERS, follow_redirects=False)
        assert r.status_code == 422
    assert counts() == before
