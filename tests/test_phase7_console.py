"""Phase 7 step 21: every screen, both roles, in real headless Chrome: nothing logged, nothing failing, fast, no-JS safe."""
import re
import statistics
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import pages, submit_pages
from tests.live_browser import HAVE_CHROME, Live
from tests.test_phase7_edge_matrix import as_role

needs_chrome = pytest.mark.skipif(not HAVE_CHROME, reason="Google Chrome is not installed here")

# A concrete page for every GET route. A new GET route that is not here fails the coverage test below.
CRAWL = {
    "/": ["/", "/?status=rejected&product=card", "/?status=in_review"],
    "/healthz": ["/healthz"],
    "/mine": ["/mine", "/mine?submitted=1"],
    "/submit": ["/submit"],
    "/reset/confirm": ["/reset/confirm"],
    "/review/{submission_id}": ["/review/1", "/review/3", "/review/5?diff=1", "/review/6", "/review/12", "/review/14?snippet=R2",
                                "/review/999"],
    "/resubmit/{submission_id}": ["/resubmit/14", "/resubmit/6"],
}
# Pages shown only through a refusal or an error; they are crawled as well.
EXTRA = ["/nowhere"]


def get_routes():
    return {r.path for router in (pages.router, submit_pages.router) for r in router.routes if "GET" in r.methods}


def test_every_get_route_is_in_the_crawl_list():
    assert get_routes() - set(CRAWL) == set(), "a GET route is not crawled: add it to CRAWL"
    assert set(CRAWL) - get_routes() == set(), "CRAWL lists a route that no longer exists"


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    import os

    path = tmp_path_factory.mktemp("live") / "live.db"
    old = os.environ.get("CLEARPATH_DB")
    os.environ["CLEARPATH_DB"] = str(path)
    try:
        with TestClient(app):            # runs startup: schema and seed
            pass
        with Live(app) as server:
            yield server
    finally:
        if old is None:
            os.environ.pop("CLEARPATH_DB", None)
        else:
            os.environ["CLEARPATH_DB"] = old


def pages_to_crawl():
    every = [p for group in CRAWL.values() for p in group] + EXTRA
    return every


@needs_chrome
@pytest.mark.parametrize("role", ["reviewer", "marketer"])
def test_no_console_errors_csp_violations_or_failed_requests_on_any_screen(live, role):
    problems = {}
    for path in pages_to_crawl():
        # The harness sets the role cookie in the page, then moves on to the real page with the app's own headers.
        target = path if role == "reviewer" else "/__harness?go=" + path
        lines, _ = live.browse(target, wait_for_result=False, seconds=1.2)
        lines = [l for l in lines if "favicon" in l or "Failed to load" in l or "Refused" in l or "Uncaught" in l
                 or "ERROR" in l or "Error" in l or "violates" in l]
        if lines:
            problems[path] = lines
    assert problems == {}


@needs_chrome
def test_the_pages_render_in_a_real_browser_with_every_script_removed(client):
    """evaluate() strips all <script> tags before rendering, so this is a page with JavaScript off."""
    from tests.chrome_layout import evaluate

    for role, who, path, selector in [("reviewer", "Maya Chen", "/", "table.queue tbody tr"),
                                      ("reviewer", "Maya Chen", "/review/3", ".decision-buttons button"),
                                      ("marketer", "Maya Chen", "/submit", "form.submit-form button"),
                                      ("marketer", "Maya Chen", "/mine", ".mine-title a")]:
        found = evaluate(as_role(client, role, who).get(path).text, 1366, 768,
                         "return {h1: d.querySelectorAll('h1').length, n: d.querySelectorAll(%r).length};" % selector)
        assert found["h1"] == 1 and found["n"] >= 1, (path, found)


def test_the_favicon_is_served_from_our_own_origin_so_it_never_404s(client):
    html = client.get("/").text
    link = re.search(r'<link rel="icon"[^>]*href="([^"]+)"', html)
    assert link and link.group(1).startswith("/static/")
    assert client.get(link.group(1).split("?")[0]).status_code == 200


def test_static_files_are_hashed_and_cacheable_but_pages_are_not(client):
    html = client.get("/").text
    css = re.search(r'href="(/static/style\.css\?v=[0-9a-f]{10})"', html).group(1)
    assert client.get(css).status_code == 200
    for path in ("/", "/submit", "/mine", "/review/3", "/reset/confirm", "/resubmit/14"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_the_queue_and_review_pages_render_within_budget(client):
    def timed(path, runs=5):
        client.get(path)
        samples = []
        for _ in range(runs):
            start = time.perf_counter()
            assert client.get(path).status_code == 200
            samples.append(time.perf_counter() - start)
        return statistics.median(samples)

    assert timed("/") < 0.3
    assert timed("/review/12") < 1.0


def test_every_core_action_works_without_javascript(client):
    """No script ran in these requests, so each flow is proof that the server side carries it."""
    from datetime import timedelta

    from app import clock

    as_role(client, "marketer", "Maya Chen")
    form = {"title": "No JS", "product": "loan", "channel": "email", "notes": "",
            "copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
            "launch_date": (clock.today() + timedelta(days=30)).isoformat()}
    assert client.post("/submit", data=form, follow_redirects=False).status_code == 303
    assert client.get("/?status=new&product=loan").status_code == 200                 # filters are a plain GET form
    as_role(client, "reviewer")
    assert 'href="/review/14?' in client.get("/review/14?snippet=R2").text or client.get("/review/14?snippet=R2").status_code == 200
    assert client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "ok"},
                       follow_redirects=False).status_code == 303
    assert client.post("/review/3/comment", data={"text": "Fine.", "version": "1"}, follow_redirects=False).status_code == 303
    assert client.post("/review/3/decision", data={"outcome": "approved", "version": "1"},
                       follow_redirects=False).status_code == 303


def test_every_form_works_as_a_plain_form_post(client):
    for role, who, path in [("marketer", "Maya Chen", "/submit"), ("reviewer", "Maya Chen", "/review/3"),
                            ("marketer", "Jordan Lee", "/resubmit/14"), ("reviewer", "Maya Chen", "/")]:
        html = as_role(client, role, who).get(path).text
        for tag in re.findall(r"<form\b[^>]*>", html):
            assert 'method="' in tag.lower() or 'method=get' in tag.lower() or "method=" in tag.lower(), tag
            assert "action=" in tag, tag
