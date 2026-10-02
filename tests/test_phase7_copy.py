"""Phase 7 steps 14 to 16: the copy inventory, the copy pattern, and the shared error pages."""
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import choices, clock, db, errors, review
from app.main import app
from app.routes import pages, submit_pages
from tests.test_phase7_edge_matrix import as_role, counts
from tests.tools import collect_copy

HEADERS = {"origin": "http://testserver"}
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def inventory():
    """Every collected (kind, text) -> screens. One crawl for the whole module (it takes a few seconds)."""
    import os

    saved = os.environ.get("CLEARPATH_DB")
    from app import clock as app_clock

    real_now = app_clock.now
    try:
        return collect_copy.collect()
    finally:
        app_clock.now = real_now
        if saved is None:
            os.environ.pop("CLEARPATH_DB", None)
        else:
            os.environ["CLEARPATH_DB"] = saved


def texts(inventory, *kinds):
    return [t for (k, t) in inventory if not kinds or k in kinds]


def visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---- step 14: the inventory ----

def test_the_inventory_file_is_current(inventory):
    assert collect_copy.INVENTORY.read_text().strip() == collect_copy.render_inventory(inventory).strip(), \
        "copy changed: run .venv/bin/python -m tests.tools.collect_copy --write"


def test_the_collector_sees_the_main_kinds_of_copy(inventory):
    kinds = {k for k, _ in inventory}
    assert {"alert", "status", "field-error", "button", "error-heading", "error-message"} <= kinds


def test_the_collector_is_deterministic(inventory):
    again = collect_copy.render_inventory(inventory)
    assert again == collect_copy.render_inventory(inventory) and "N" in again


def test_the_extractor_finds_alerts_buttons_and_ignores_plain_text():
    html = ('<p>plain</p><div role="alert"><p>Fix 2 things.</p></div><button type="submit">Save</button>'
            '<p class="empty-state">Nothing <a href="/">Go</a></p>')
    assert collect_copy.extract(html) == {("alert", "Fix N things."), ("button", "Save"), ("empty-state", "Nothing Go")}


EMPTY_SCREENS = [("reviewer", "/?status=rejected&product=card"), ("marketer", "/?status=rejected&product=card"),
                 ("marketer", "/mine"), ("reviewer", "/mine")]


@pytest.mark.parametrize("role,path", EMPTY_SCREENS)
def test_empty_states_offer_a_next_action(client, role, path):
    if path == "/mine":
        client.cookies.set("marketer", "Sam Patel")                     # Sam has no submissions
    html = as_role(client, role).get(path).text
    found = re.findall(r'<p class="empty-state"[^>]*>(.*?)</p>', html, re.S)
    assert found, "expected an empty state on " + path
    for block in found:
        # A reviewer on /mine cannot submit for anyone: the next action is the marketer picker on the same page.
        picker = "marketer-picker" in html and "Choose another marketer above." in block
        assert "<a " in block or "<button" in block or picker, "empty state with no next action: " + visible(block)


def test_the_empty_queue_offers_to_submit(client):
    with db.connect() as c:
        c.execute("DELETE FROM submission")
    for role in ("reviewer", "marketer"):
        html = as_role(client, role).get("/").text
        assert re.search(r'class="empty-state"[^>]*>[^<]*<a href="/submit"', html)


def test_a_thread_with_no_comments_tells_a_reviewer_what_to_do(client):
    with db.connect() as c:
        c.execute("DELETE FROM comment")
    assert "No comments yet. Add the first one below." in client.get("/review/3").text


# Words and marks that make copy sound broken, blaming or noisy, and anything that leaks internals.
BANNED = ["oops", "sorry", "uh oh", "whoops", "error occurred", "something went wrong", "your fault", "invalid",
          "illegal", "traceback", "exception", "errno", "sqlite", "select ", "/users/", ".py", "internal server",
          "undefined", "null", "nan"]


def test_no_copy_sounds_broken_or_leaks_internals(inventory):
    for text in texts(inventory):
        low = " %s " % text.lower()
        assert "!" not in text, text
        for word in BANNED:
            assert not re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(word.strip()) if word.isalpha() or word.strip().isalpha()
                                 else re.escape(word), low), "%r contains %r" % (text, word)


def test_no_copy_is_empty_or_absurdly_long(inventory):
    for text in texts(inventory, "alert", "field-error", "empty-state", "error-message", "button"):
        assert 2 <= len(text) <= 420, text


# ---- step 15: one pattern, one vocabulary ----

def test_apostrophes_are_straight_everywhere(inventory):
    assert [t for t in texts(inventory) if "’" in t or "‘" in t] == []
    assert not re.search(r"&rsquo;|&lsquo;|’|‘", "".join(
        p.read_text() for p in (ROOT / "app" / "templates").glob("*.html")))


def test_length_limits_are_always_worded_or_fewer(inventory):
    assert [t for t in texts(inventory) if re.search(r"\bunder [\dN,]+ characters", t)] == []
    assert review.DECISION_MESSAGES["reason_too_long"] == "Keep the reason to 2,000 characters or fewer."


SYNONYMS = ["send back", "sent back", "kick back", "turn down", "decline", "deny", "denied", "bounce", "pass on"]


def test_one_word_per_concept(inventory):
    sources = [p.read_text() for p in (ROOT / "app" / "templates").glob("*.html")]
    blob = " ".join(sources + texts(inventory)).lower()
    for word in SYNONYMS:
        assert not re.search(r"(?<![a-z])%s(?![a-z])" % word, re.sub(r"\{[#%].*?[#%]\}", "", blob)), word
    buttons = set(texts(inventory, "button"))
    assert {"Approve", "Request changes", "Reject", "Post comment", "Dismiss flag"} <= buttons


def test_every_refusal_that_a_role_can_fix_says_how(inventory):
    for text in texts(inventory, "error-message"):
        if text.startswith("Only "):
            assert "Switch role" in text, text
        if "must be made from this site" in text or "must be started from this site" in text:
            assert "Reload the page and try again." in text, text


def test_a_conflict_banner_says_to_reload(inventory):
    stale = [t for t in texts(inventory, "alert") if "A newer version exists" in t]
    assert stale and all("Reload the page to see the latest version." in t for t in stale)


def test_urgent_queue_labels_all_start_with_rush_or_overdue(client):
    html = client.get("/").text
    labels = re.findall(r'<span class="urgency">(.*?)</span>', html)
    assert labels and all(l.startswith(("Rush:", "Overdue")) for l in labels), labels


NOTES = ["Flags assist the reviewer. They never decide.", "Rules are illustrative, not legal advice."]


def test_the_two_fixed_notes_appear_verbatim_on_review_and_precheck(client):
    review_page = visible(client.get("/review/3").text)
    assert all(n in review_page for n in NOTES)
    as_role(client, "marketer", "Maya Chen")
    check = client.post("/submit/check", headers=HEADERS,
                        data={"product": "loan", "channel": "email", "copy": "Guaranteed approval."}).text
    assert "Rules are illustrative, not legal advice." in visible(check) and "Flags don't block submitting." in visible(check)


def test_names_ids_statuses_and_rules_are_unchanged(client):
    assert choices.STATUSES == ("new", "in_review", "changes_requested", "approved", "rejected")
    assert choices.OUTCOMES == ("approved", "changes_requested", "rejected")
    assert [r["id"] for r in json.loads((ROOT / "data" / "rules.json").read_text())] == \
        ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    names, ids = set(), set()
    for role, marketer in (("reviewer", "Maya Chen"), ("marketer", "Maya Chen"), ("marketer", "Jordan Lee")):
        as_role(client, role, marketer)
        for path in ["/", "/submit", "/mine", "/reset/confirm", "/resubmit/14", "/resubmit/8", "/review/3",
                     "/review/12", "/review/14?snippet=R2", "/review/5?diff=1"]:
            html = client.get(path).text
            names |= set(re.findall(r'<(?:input|select|textarea|button)[^>]*\bname="([^"]+)"', html))
            ids |= set(re.findall(r'\bid="([^"]+)"', html))
    assert names == {"action", "base_version", "channel", "confirm", "copy", "launch_date", "name", "note", "notes",
                     "outcome", "product", "reason", "role", "rule_id", "status", "text", "title", "version"}
    assert {"comment-form", "comment-text", "copy-count", "snippet-note"} <= ids
    js = "".join(p.read_text() for p in (ROOT / "app" / "static").glob("*.js") if "htmx" not in p.name)
    for used in set(re.findall(r"getElementById\(['\"]([^'\"]+)", js)):
        assert used in ids, "script needs #%s" % used


def test_hostile_text_stays_escaped_inside_the_new_copy(client):
    evil = '"><img src=x onerror=alert(1)><script>alert(2)</script>'
    as_role(client, "marketer", "Maya Chen")
    r = client.post("/submit", headers=HEADERS, follow_redirects=True, data={
        "title": evil, "product": "loan", "channel": "email", "notes": "",
        "copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
    assert r.status_code == 200 and "<img src=x" not in r.text and "<script>alert(2)" not in r.text
    assert "&lt;script&gt;alert(2)" in r.text
    dup = client.post("/submit", headers=HEADERS, data={"title": evil, "product": "loan", "channel": "email",
        "notes": "", "copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
    assert "<img src=x" not in dup.text and "You already submitted this." in dup.text


def test_no_template_marks_user_text_safe():
    for path in (ROOT / "app" / "templates").glob("*.html"):
        assert "|safe" not in path.read_text() and "Markup(" not in path.read_text(), path.name
    assert not re.search(r"Markup\(", "".join(p.read_text() for p in (ROOT / "app").rglob("*.py")))


# ---- step 16: the shared error page ----

def assert_error_page(response, status, heading=None, headers=True):
    assert response.status_code == status
    html = response.text
    assert html.count("<h1") == 1 and 'class="error-heading"' in html
    assert re.search(r'<p class="error-status">Error %d</p>' % status, html)
    assert 'class="error-message"' in html and 'href="/">Back to the queue</a>' in html
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    if headers:
        assert response.headers["x-content-type-options"] == "nosniff" and "frame-ancestors" in \
            response.headers["content-security-policy"]
    if heading:
        assert heading in html
    for leak in ("Traceback", "Exception", "starlette", "fastapi", "uvicorn", "sqlite", "/Users/", ".py\"", "detail"):
        assert leak not in html, leak


def test_400_from_a_bad_role(client):
    assert_error_page(client.post("/role", data={"role": "admin"}), 400, "Request not understood")


def test_403_from_a_role_refusal_and_from_a_foreign_origin(client):
    as_role(client, "marketer", "Maya Chen")
    assert_error_page(client.post("/review/3/decision", data={"outcome": "approved", "version": "1"},
                                  headers=HEADERS), 403, "Not allowed")
    assert_error_page(client.post("/submit", data={}, headers={"origin": "https://evil.example"}), 403)


def test_404_for_unknown_pages_and_ids(client):
    assert_error_page(client.get("/nowhere"), 404, "Page not found")
    for path in ["/review/999999", "/review/abc", "/review/-1", "/review/0", "/review/1e3", "/review/%00",
                 "/review/" + "9" * 400, "/resubmit/999999", "/resubmit/abc", "/review/1/extra", "/review/%E2%80%AE"]:
        assert_error_page(client.get(path), 404)
    for path in ["/review/999999/decision", "/review/abc/dismiss", "/review/0/comment"]:
        assert_error_page(client.post(path, data={}, headers=HEADERS), 404)


def test_405_has_an_allow_header_on_every_route_and_a_wrong_method(client):
    seen = 0
    for route in pages.router.routes + submit_pages.router.routes:
        methods = getattr(route, "methods", None)
        if not methods or route.path.startswith("/static"):
            continue
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        wrong = "PUT" if "PUT" not in methods else "PATCH"
        r = client.request(wrong, path, headers=HEADERS)
        assert_error_page(r, 405, "Action not available")
        allowed = set(m.strip() for m in r.headers["allow"].split(","))
        assert allowed and allowed <= {"GET", "HEAD", "POST"}
        seen += 1
    assert seen >= 15


def test_409_from_a_second_decision(client):
    client.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS)
    r = client.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS)
    assert r.status_code == 409 and "Your decision was not saved." in r.text      # banner on the review page itself


def test_413_for_an_oversized_body(client):
    as_role(client, "marketer", "Maya Chen")
    before = counts()
    r = client.post("/submit", content=b"x=" + b"a" * (1024 * 1024 + 5),
                    headers={**HEADERS, "content-type": "application/x-www-form-urlencoded"})
    assert_error_page(r, 413, "Request too large")
    assert counts() == before
    lying = client.post("/submit", content=b"x=1", headers={**HEADERS, "content-length": str(2 * 1024 * 1024),
                        "content-type": "application/x-www-form-urlencoded"})
    assert lying.status_code in (400, 413)


def test_422_for_a_request_that_cannot_be_read():
    mini = FastAPI(docs_url=None, openapi_url=None)
    mini.add_exception_handler(errors.StarletteHTTPException, errors.http_error)
    mini.add_exception_handler(errors.RequestValidationError, errors.validation_error)

    @mini.get("/n/{number}")
    def n(number: int):
        return {"n": number}

    with TestClient(mini) as c:
        assert_error_page(c.get("/n/notanumber"), 422, "Could not read the request", headers=False)
        assert "number" not in c.get("/n/notanumber").text.lower().replace("could not read", "")


def test_429_names_the_wait_and_sets_retry_after(client):
    pages.reset_cooldown.clear()
    assert client.post("/reset", data={"confirm": "reset"}, headers=HEADERS, follow_redirects=False).status_code == 303
    r = client.post("/reset", data={"confirm": "reset"}, headers=HEADERS)
    assert_error_page(r, 429, "Please wait a moment")
    assert int(r.headers["retry-after"]) >= 1


def test_500_is_fixed_text_rolled_back_and_the_lock_is_released(db_path, monkeypatch):
    secret = "secret-path /etc/passwd SELECT * FROM x"

    def boom(conn, *args, **kwargs):
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM comment")
        raise RuntimeError(secret)

    with TestClient(app, raise_server_exceptions=False) as c:
        before = counts()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.routes.pages.review.record_decision", boom)
            r = c.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS)
        assert_error_page(r, 500, "We hit a problem")
        assert "secret-path" not in r.text and "RuntimeError" not in r.text and "passwd" not in r.text
        assert counts() == before                                  # the delete was rolled back
        ok = c.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=HEADERS,
                    follow_redirects=False)
        assert ok.status_code == 303                               # the write lock was released


def test_500_falls_back_to_a_plain_page_if_the_template_fails(db_path, monkeypatch):
    import app.errors as errors_module

    def broken(*args, **kwargs):
        raise RuntimeError("template exploded")

    with TestClient(app, raise_server_exceptions=False) as c:
        monkeypatch.setattr("app.routes.pages.list_queue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        monkeypatch.setattr(errors_module, "render", broken)
        r = c.get("/")
    assert r.status_code == 500 and "We hit a problem" in r.text and "template exploded" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"


def test_head_and_options_behave(client):
    for path in ["/", "/submit", "/review/3", "/mine", "/healthz", "/nowhere", "/reset/confirm"]:
        head = client.head(path)
        assert head.status_code < 500 and head.content == b""
        assert client.options(path).status_code < 500


def test_the_error_page_is_the_same_for_both_roles_and_links_home(client):
    for role in ("reviewer", "marketer"):
        r = as_role(client, role).get("/nowhere")
        assert_error_page(r, 404, "We could not find that page.")
