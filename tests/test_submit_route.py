import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app import clock, db, submit

GOOD = {"title": "Spring promo", "product": "loan", "channel": "email", "copy": "Guaranteed approval, 5.99%.",
        "notes": "First draft."}


def good(**over):
    data = dict(GOOD, launch_date=(clock.today() + timedelta(days=30)).isoformat())
    data.update(over)
    return data


def post(client, data=None, headers=None, **kw):
    return client.post("/submit", data=good() if data is None else data, headers=headers or {},
                       follow_redirects=False, **kw)


def count():
    with db.connect() as c:
        return c.execute("SELECT count(*) FROM submission").fetchone()[0]


def errors_in(html):
    return re.findall(r'<p class="field-error" id="error-(\w+)">(.*?)</p>', html)


# ---- the pages ----

def test_marketer_sees_the_form(mclient):
    r = mclient.get("/submit")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.text.count("<h1") == 1 and 'action="/submit"' in r.text and 'method="post"' in r.text
    assert "Submitting as <strong>Maya Chen</strong>" in r.text
    assert "error-copy" not in r.text and 'aria-invalid' not in r.text   # no dict.copy lookalike errors
    assert re.search(r'rows="12">\n</textarea>', r.text)


def test_reviewer_sees_an_explanation_and_no_form(client):
    r = client.get("/submit")
    assert r.status_code == 200 and "<form method=\"post\" action=\"/submit\"" not in r.text
    assert 'action="/submit"' not in r.text
    assert "Only marketers submit copy" in r.text and "Switch to Marketer" in r.text


def test_every_control_has_a_label_and_nothing_is_inline(mclient):
    html = mclient.get("/submit").text
    for name in ("title", "product", "channel", "launch_date", "copy", "notes"):
        assert 'id="field-%s"' % name in html and 'for="field-%s"' % name in html
    assert not re.search(r"\son\w+=", html) and ' style="' not in html
    assert 'maxlength="120"' in html


def test_query_values_are_not_reflected(mclient):
    html = mclient.get("/submit?title=<script>alert(1)</script>&copy=zzzMARKER").text
    assert "zzzMARKER" not in html and "<script>alert(1)" not in html


# ---- success ----

def test_a_valid_submission_is_created_and_redirects(mclient):
    r = post(mclient)
    assert r.status_code == 303 and r.headers["cache-control"] == "no-store"
    loc = r.headers["location"]
    assert re.fullmatch(r"/mine\?submitted=\d+", loc)
    sid = int(loc.split("=")[1])
    with db.connect() as c:
        row = c.execute("SELECT status, submitted_by, current_version, title FROM submission WHERE id = ?",
                        (sid,)).fetchone()
        flags = c.execute("SELECT count(DISTINCT rule_id) FROM flag f JOIN version v ON v.id = f.version_id"
                          " WHERE v.submission_id = ?", (sid,)).fetchone()[0]
    assert tuple(row) == ("new", "Maya Chen", 1, "Spring promo") and flags >= 2
    queue = mclient.get("/").text
    assert "Spring promo" in queue and "Maya Chen" in queue
    for _ in range(2):                              # refreshing the target never resubmits
        assert mclient.get(loc).status_code == 200
    assert count() == 15


def test_submitted_by_follows_the_chosen_marketer(mclient):
    mclient.post("/marketer", data={"name": "Jordan Lee"})
    sid = int(post(mclient).headers["location"].split("=")[1])
    with db.connect() as c:
        assert c.execute("SELECT submitted_by FROM submission WHERE id = ?", (sid,)).fetchone()[0] == "Jordan Lee"


def test_past_and_rush_dates_still_submit(mclient):
    past = (clock.today() - timedelta(days=3)).isoformat()
    assert post(mclient, good(launch_date=past, title="Past")).status_code == 303
    assert post(mclient, good(launch_date=clock.today().isoformat(), title="Today")).status_code == 303
    assert count() == 16


def test_crlf_from_a_browser_is_stored_as_lf(mclient):
    sid = int(post(mclient, good(copy="Line one\r\nLine two\r\n")).headers["location"].split("=")[1])
    with db.connect() as c:
        assert c.execute("SELECT copy FROM version WHERE submission_id = ?", (sid,)).fetchone()[0] == "Line one\nLine two"


# ---- validation errors ----

def test_every_field_missing_gives_one_message_each_and_keeps_nothing_written(mclient):
    r = post(mclient, {})
    assert r.status_code == 422 and r.headers["cache-control"] == "no-store"
    assert [n for n, _ in errors_in(r.text)] == ["title", "product", "channel", "launch_date", "copy"]
    assert 'role="alert"' in r.text and "autofocus" in r.text and count() == 14
    assert "Fix 5 things below" in r.text


def test_a_failed_submission_keeps_what_was_typed(mclient):
    data = good(title="  Kept <b>title</b> ", copy="Line one\r\n\r\nLine \"two\" & more", notes="Note\nlines",
                product="card", channel="display", launch_date="not a date")
    r = post(mclient, data)
    assert r.status_code == 422 and [n for n, _ in errors_in(r.text)] == ["launch_date"]
    assert 'value="  Kept &lt;b&gt;title&lt;/b&gt; "' in r.text
    assert re.search(r'<option value="card" selected>', r.text) and re.search(r'<option value="display" selected>', r.text)
    assert "Line one\r\n\r\nLine &#34;two&#34; &amp; more</textarea>" in r.text or \
        "Line one\n\nLine &#34;two&#34; &amp; more</textarea>" in r.text
    assert "Note\nlines</textarea>" in r.text or "Note\r\nlines</textarea>" in r.text
    assert "<b>title</b>" not in r.text


def test_errors_link_to_their_fields_and_mark_them_invalid(mclient):
    r = post(mclient, good(title=""))
    assert 'href="#field-title"' in r.text and 'aria-invalid="true" aria-describedby="error-title"' in r.text


def test_a_leading_blank_line_in_the_copy_survives_a_rerender(mclient):
    r = post(mclient, good(copy="\n\nAfter blanks", title=""))
    assert 'rows="12">\n\n\nAfter blanks</textarea>' in r.text   # one newline is the parser's to drop


def test_hostile_text_is_escaped_everywhere(mclient):
    evil = "\"><script>alert(1)</script></textarea><img src=x onerror=alert(1)>"
    r = post(mclient, good(title=evil, copy=evil, notes=evil, launch_date="bad"))
    assert r.status_code == 422
    assert "<script>alert(1)" not in r.text and "<img src=x" not in r.text
    assert r.text.count("<textarea") == 2


@pytest.mark.parametrize("make", [
    lambda c: c.post("/submit", json=good(), follow_redirects=False),
    lambda c: c.post("/submit", files={"copy": ("x.txt", b"file body")}, data={k: v for k, v in good().items() if k != "copy"},
                     follow_redirects=False),
    lambda c: c.post("/submit", content=b"title=a&title=b&product=loan&channel=email&copy=x&launch_date=2030-01-01",
                     headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False),
    lambda c: c.post("/submit", content=b"product=loan&product=card&channel=email",
                     headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False),
    lambda c: c.post("/submit", content=b"\xff\xfe\x00garbage", headers={"content-type": "application/x-www-form-urlencoded"},
                     follow_redirects=False),
])
def test_wrong_shapes_are_422_never_500_and_never_json(mclient, make):
    r = make(mclient)
    assert r.status_code == 422 and "text/html" in r.headers["content-type"]
    assert count() == 14 and "Traceback" not in r.text and "detail" not in r.text[:50]


def test_a_two_megabyte_body_is_refused(mclient):
    r = post(mclient, good(copy="a" * (2 * 1024 * 1024)))
    assert r.status_code == 413 and count() == 14


def test_extra_fields_are_ignored(mclient):
    r = post(mclient, good(submitted_by="Mallory", status="approved", current_version="9", created_at="1999-01-01T00:00:00Z",
                           id="1", flags="R1", reviewer="Mallory"))
    sid = int(r.headers["location"].split("=")[1])
    with db.connect() as c:
        row = c.execute("SELECT id, status, current_version, created_at, submitted_by FROM submission WHERE id = ?",
                        (sid,)).fetchone()
        assert row["id"] == sid and row["id"] != 1
        assert (row["status"], row["current_version"], row["submitted_by"]) == ("new", 1, "Maya Chen")
        assert not row["created_at"].startswith("1999")


# ---- guards ----

def test_reviewer_role_is_refused_and_nothing_is_written(client):
    client.cookies.set("role", "reviewer")
    r = post(client)
    assert r.status_code == 403 and "Only marketers can submit." in r.text and count() == 14


def test_a_missing_or_forged_role_cookie_counts_as_reviewer(client):
    assert post(client).status_code == 403
    client.cookies.set("role", "admin")
    assert post(client).status_code == 403 and count() == 14


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_cross_origin_is_refused(mclient, origin):
    r = post(mclient, headers={"Origin": origin})
    assert r.status_code == 403 and count() == 14


def test_matching_and_absent_origin_work(mclient):
    assert post(mclient, headers={"Origin": "http://testserver"}).status_code == 303
    assert post(mclient, good(title="Second")).status_code == 303


def test_only_post_is_allowed_on_the_check_and_marketer_urls(mclient):
    assert mclient.put("/submit", data=good()).status_code == 405
    assert mclient.delete("/submit").status_code == 405


# ---- duplicates and capacity ----

def test_the_same_submission_twice_is_refused_with_a_link(mclient):
    first = post(mclient)
    sid = first.headers["location"].split("=")[1]
    second = post(mclient)
    assert second.status_code == 409 and "You already submitted this." in second.text
    assert 'href="/review/%s"' % sid in second.text and count() == 15
    assert 'value="Spring promo"' in second.text


def test_a_concurrent_double_submit_makes_one_submission(mclient):
    def go(_):
        return post(mclient).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(go, range(8)))
    assert codes.count(303) == 1 and codes.count(409) == 7 and count() == 15


def test_capacity_is_a_friendly_error_that_keeps_the_input(mclient):
    with db.connect() as c:
        c.executemany(
            "INSERT INTO submission (title, product, channel, status, launch_date, submitted_by, created_at,"
            " current_version) VALUES (?, 'loan', 'email', 'new', '2030-01-01', 'Maya Chen', '2026-10-01T00:00:00Z', 1)",
            [("Filler %d" % i,) for i in range(submit.MAX_SUBMISSIONS - 14)])
    r = post(mclient)
    assert r.status_code == 503 and "The demo is full right now" in r.text and 'value="Spring promo"' in r.text
    assert count() == submit.MAX_SUBMISSIONS


# ---- response hygiene ----

@pytest.mark.parametrize("make", [
    lambda c: c.get("/submit"), lambda c: post(c), lambda c: post(c, {}), lambda c: post(c, headers={"Origin": "null"}),
])
def test_security_headers_on_every_outcome(mclient, make):
    r = make(mclient)
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_duplicate_response_has_the_headers_too(mclient):
    post(mclient)
    r = post(mclient)
    assert r.status_code == 409 and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_error_pages_leak_nothing(mclient):
    for r in (post(mclient, {}), post(mclient, good(launch_date="x"))):
        for leak in ("Traceback", "sqlite", "/Users/", "File \"", "SELECT "):
            assert leak not in r.text


# ---- step 6: warnings on the form, without JavaScript ----

def check_page(client, launch):
    return client.post("/submit/check", data=good(launch_date=launch.isoformat()))


@pytest.mark.parametrize("days", range(-3, 12))
def test_the_form_warning_matches_the_queue_rule(mclient, days):
    launch = clock.today() + timedelta(days=days)
    r = check_page(mclient, launch)
    has_warning = 'class="field-warning"' in r.text
    assert has_warning == (clock.urgency(clock.today(), launch, "new") is not None)


def test_warning_wording(mclient):
    today = clock.today()
    past = check_page(mclient, today - timedelta(days=2)).text
    assert "already passed" in past and "business day" not in past
    far = check_page(mclient, today + timedelta(days=30)).text
    assert "field-warning" not in far
    near = check_page(mclient, today + timedelta(days=1)).text
    assert "rush review may not finish in time" in near


def test_warnings_do_not_stop_the_form_and_hold_no_user_text(mclient):
    r = mclient.post("/submit/check", data=good(launch_date=clock.today().isoformat(), title="<i>mine</i>"))
    warning = re.search(r'<p class="field-warning">(.*?)</p>', r.text, re.S).group(1)
    assert "mine" not in warning and 'class="submit-button"' in r.text


def test_check_flags_with_errors_still_shows_the_errors_and_panel_rules(mclient):
    r = mclient.post("/submit/check", data={"product": "loan", "channel": "email", "copy": "Guaranteed approval"})
    assert [n for n, _ in errors_in(r.text)] == ["title", "launch_date"] and "R1: " in r.text
    r = mclient.post("/submit/check", data={"copy": "Guaranteed approval"})
    assert "Choose a product, channel and add copy to see flags." in r.text and "R1: " not in r.text


# ---- step 7: the success banner ----

def banner(html):
    m = re.search(r'<div class="notice banner-submitted" role="status">(.*?)</div>', html, re.S)
    return m.group(1) if m else None


def test_banner_after_a_real_submit(mclient):
    loc = post(mclient, good(title="<b>Bold</b> title", launch_date=(clock.today() + timedelta(days=1)).isoformat())
               ).headers["location"]
    html = mclient.get(loc).text
    text = banner(html)
    assert "&lt;b&gt;Bold&lt;/b&gt; title" in text and "<b>Bold" not in text
    assert "rush review may not finish in time" in text and "/review/" in text


def test_no_warning_in_the_banner_for_a_far_date(mclient):
    assert "field-warning" not in banner(mclient.get(post(mclient).headers["location"]).text)


@pytest.mark.parametrize("query", ["abc", "0", "-1", "9999", "<script>", "1&submitted=2", "x" * 5000, "", "3 ", "1.5"])
def test_bad_banner_ids_show_nothing_and_echo_nothing(mclient, query):
    r = mclient.get("/mine?submitted=" + query)
    assert r.status_code == 200 and banner(r.text) is None
    assert "<script>" not in r.text and "xxxxx" not in r.text


def test_banner_is_only_for_the_owner(mclient):
    loc = post(mclient).headers["location"]
    assert banner(mclient.get(loc).text)
    mclient.post("/marketer", data={"name": "Jordan Lee"})
    assert banner(mclient.get(loc).text) is None
    assert banner(mclient.get("/mine?submitted=1").text) is None   # #1 is Maya's, and the viewer is Jordan


def test_the_banner_survives_a_refresh(mclient):
    loc = post(mclient).headers["location"]
    assert banner(mclient.get(loc).text) == banner(mclient.get(loc).text)


@pytest.mark.parametrize("days", range(-3, 12))
def test_the_form_warning_agrees_with_the_queue_label_for_the_same_date(mclient, days):
    """Submit an item with this launch date, then compare the queue's own label with the form's warning."""
    from app.queue import list_queue, row_view

    launch = clock.today() + timedelta(days=days)
    sid = int(post(mclient, good(title="Label %d" % days, launch_date=launch.isoformat())).headers["location"].split("=")[1])
    with db.connect() as c:
        row = next(r for r in list_queue(c) if r["id"] == sid)
    label = row_view(row, clock.today())["urgency_label"] or ""
    warning = re.findall(r'<p class="field-warning">(.*?)</p>', check_page(mclient, launch).text, re.S)
    text = re.sub(r"<[^>]+>|&#9888;", "", warning[0]).strip() if warning else ""
    assert bool(label) == bool(text), (label, text)
    if label.startswith("Overdue"):
        assert "already passed" in text
    elif label == "Launches today":
        assert "Launches today." in text
    elif label == "Launches tomorrow":
        assert "Launches in 1 business day." in text or "Launches before the next business day." in text
    elif label == "Rush: launches this weekend":
        assert "Launches before the next business day." in text
    elif label.startswith("Rush: launches in "):
        n = label.split("in ")[1].split(" business")[0]
        assert "Launches in %s business day" % n in text
