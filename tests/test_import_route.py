from markupsafe import escape as html_escape
import re
import sqlite3
from datetime import date

import pytest
from openpyxl import Workbook

from app import xlsx_import
from app.routes import import_pages
from app.security import CSP, MAX_BODY_BYTES

from tests.test_xlsx_import import COPY, HEAD, book, good

ORIGIN = {"origin": "http://testserver"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def _clean_pending():
    import_pages.pending.clear()
    yield
    import_pages.pending.clear()


def count(db_path, sql="SELECT count(*) FROM submission", args=()):
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


def upload(client, data, name="rows.xlsx", headers=ORIGIN):
    return client.post("/import/preview", files={"file": (name, data, XLSX)}, headers=headers)


def sample(client, headers=ORIGIN):
    return client.post("/import/preview", data={"source": "sample"}, headers=headers)


def token_of(r):
    return re.search(r'name="token" value="([^"]+)"', r.text).group(1)


def confirm(client, token, headers=ORIGIN):
    return client.post("/import/confirm", data={"token": token}, headers=headers, follow_redirects=False)


def future(days):
    from datetime import timedelta
    return date.today() + timedelta(days=days)


def assert_secure(r):
    assert r.headers["content-security-policy"] == CSP and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


# ---- the page ----

def test_import_page_for_a_marketer(mclient):
    r = mclient.get("/import")
    assert r.status_code == 200 and "Preview the sample file" in r.text and 'enctype="multipart/form-data"' in r.text
    assert 'href="/import"' in r.text and "Importing as <strong>Maya Chen</strong>" in r.text
    assert_secure(r)


def test_import_page_for_a_reviewer_says_why_and_offers_the_switch(client):
    r = client.get("/import")
    assert r.status_code == 200 and "Only marketers and affiliate partners import" in r.text
    assert "Preview the sample file" not in r.text and 'href="/import"' not in r.text.split("<main")[0]


def test_nav_and_submit_page_link_to_the_import(mclient):
    assert 'href="/import"' in mclient.get("/submit").text
    assert "Have many?" in mclient.get("/submit").text


def test_manual_submit_is_still_there(mclient):
    assert 'action="/submit"' in mclient.get("/submit").text


# ---- the sample, end to end ----

def test_sample_preview_then_import(mclient, db_path):
    before = count(db_path)
    r = sample(mclient)
    assert r.status_code == 200 and "5 ready" in r.text and "4 need fixing" in r.text
    assert "1 already exists" in r.text and "Import 5 rows" in r.text
    assert count(db_path) == before  # a preview writes nothing
    for text in ("Ready", "Needs fixing", "Already exists", "Add a title.", "Choose a product.",
                 "R1: ", "R7: ", "Open it"):
        assert text in r.text
    assert_secure(r)
    done = confirm(mclient, token_of(r))
    assert done.status_code == 303 and done.headers["location"] == "/mine?imported=5&skipped=1&failed=4"
    assert count(db_path) == before + 5
    page = mclient.get(done.headers["location"])
    assert "Imported 5 submissions." in page.text and "Auto loan referral email" in page.text
    assert "Spring loan promo email" in mclient.get("/").text  # in the queue, as new items


def test_importing_the_sample_twice_skips_everything(mclient, db_path):
    confirm(mclient, token_of(sample(mclient)))
    again = sample(mclient)
    assert "0 ready" in again.text and "6 already exist" in again.text and "Nothing new to import" in again.text
    assert "Import 0" not in again.text and 'name="token"' not in again.text


def test_reset_removes_imports_so_the_sample_works_again(mclient, db_path):
    confirm(mclient, token_of(sample(mclient)))
    assert mclient.post("/reset", data={"confirm": "reset"}, headers=ORIGIN).status_code in (200, 303)
    assert count(db_path) == 14
    assert "5 ready" in sample(mclient).text


def test_sample_download(mclient):
    r = mclient.get("/import/sample.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK" and "attachment" in r.headers["content-disposition"]
    assert xlsx_import.parse_workbook(r.content)


def test_missing_sample_file_gives_a_fixed_message(mclient, monkeypatch, tmp_path):
    monkeypatch.setattr(xlsx_import, "SAMPLE_PATH", tmp_path / "nope.xlsx")
    r = sample(mclient)
    assert r.status_code == 422 and html_escape(xlsx_import.FILE_MESSAGES["sample_missing"]) in r.text
    assert "nope" not in r.text and "Traceback" not in r.text
    assert mclient.get("/import/sample.xlsx").status_code == 404


# ---- uploads ----

def test_upload_preview_and_import_as_the_current_marketer(mclient, db_path):
    mclient.cookies.set("marketer", "Sam Patel")
    data = book([good(Title="Mine"), good(Title="Also mine")] + [good() + []][:0])
    r = upload(mclient, data)
    assert "2 ready" in r.text
    assert confirm(mclient, token_of(r)).headers["location"].startswith("/mine?imported=2")
    assert count(db_path, "SELECT count(*) FROM submission WHERE submitted_by = 'Sam Patel'") == 2
    assert "Mine" in mclient.get("/mine").text
    mclient.cookies.set("marketer", "Maya Chen")
    assert "Also mine" not in mclient.get("/mine").text


def test_submitted_by_in_the_file_is_ignored(mclient, db_path):
    data = book([good() + ["Jordan Lee"]], head=HEAD + ["Submitted by"])
    confirm(mclient, token_of(upload(mclient, data)))
    assert count(db_path, "SELECT submitted_by FROM submission ORDER BY id DESC LIMIT 1") == "Maya Chen"


def test_cell_text_is_escaped_in_the_preview_and_after(mclient):
    evil = '<script>alert(1)</script> {{ 7*7 }}'
    r = upload(mclient, book([good(Title=evil)]))
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text and "49" not in r.text
    confirm(mclient, token_of(r))
    assert "<script>alert(1)" not in mclient.get("/mine").text and "<script>alert(1)" not in mclient.get("/").text


def test_error_rows_never_echo_cell_text(mclient):
    r = upload(mclient, book([good(Product="SECRET-PRODUCT", **{"Launch date": "SECRET-DATE"})]))
    assert "SECRET-PRODUCT" not in r.text and "SECRET-DATE" not in r.text and "Choose a product." in r.text


@pytest.mark.parametrize("data, name, text", [
    (None, None, "No file was chosen"),
    (b"", "empty.xlsx", "No file was chosen"),
    (b"a,b\n1,2\n", "rows.xlsx", "isn't an Excel .xlsx file"),
    (b"\xd0\xcf\x11\xe0" + b"0" * 600, "locked.xlsx", "isn't an Excel .xlsx file"),
    (book([]), "headers-only.xlsx", "No rows found"),
    (book([["a"]], head=["Title"]), "bad.xlsx", "needs these column headers: Product, Channel, Launch date, Copy"),
])
def test_bad_uploads_give_a_fixed_message_and_the_form_again(mclient, db_path, data, name, text):
    r = mclient.post("/import/preview", headers=ORIGIN, **({"files": {"file": (name, data, XLSX)}} if data is not None
                                                          else {"data": {}}))
    assert r.status_code == 422 and html_escape(text) in r.text and 'role="alert"' in r.text
    assert "Preview the sample file" in r.text and count(db_path) == 14 and 'name="token"' not in r.text


def test_oversize_upload_is_a_413(mclient):
    r = upload(mclient, b"0" * (MAX_BODY_BYTES + 10))
    assert r.status_code == 413 and "too large" in r.text.lower()


def test_text_field_posted_as_file_or_twice_is_not_a_source(mclient):
    r = mclient.post("/import/preview", data={"source": ["sample", "sample"]}, headers=ORIGIN)
    assert r.status_code == 422  # not the sample: falls through to "no file"


def test_all_rows_invalid_offers_no_import(mclient):
    r = upload(mclient, book([good(Title=None)]))
    assert r.status_code == 200 and "0 ready" in r.text and 'name="token"' not in r.text
    assert "Fix the rows" in r.text


# ---- the confirm step ----

def test_confirm_is_single_use(mclient, db_path):
    token = token_of(sample(mclient))
    assert confirm(mclient, token).status_code == 303
    n = count(db_path)
    second = confirm(mclient, token)  # a double click
    assert second.status_code == 409 and "already imported" in second.text and count(db_path) == n


@pytest.mark.parametrize("token", ["", "nope", "x" * 5000])
def test_confirm_with_unknown_token(mclient, db_path, token):
    r = confirm(mclient, token)
    assert r.status_code == 409 and "Nothing was imported" in r.text and count(db_path) == 14


def test_confirm_without_a_token_field(mclient):
    assert mclient.post("/import/confirm", data={}, headers=ORIGIN).status_code == 409


def test_token_only_works_for_the_one_who_previewed(mclient, db_path):
    token = token_of(sample(mclient))
    mclient.cookies.set("marketer", "Jordan Lee")
    assert confirm(mclient, token).status_code == 409
    mclient.cookies.set("marketer", "Maya Chen")
    assert confirm(mclient, token).status_code == 303  # the refusal did not use it up


def test_token_expires(mclient, monkeypatch, db_path):
    from datetime import timedelta

    from app import clock

    token = token_of(sample(mclient))
    later = clock.now() + import_pages.PREVIEW_TTL + timedelta(seconds=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert confirm(mclient, token).status_code == 409 and count(db_path) == 14


def test_pending_previews_are_capped():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    store = import_pages.PendingImports()
    tokens = [store.put("Maya Chen", b"x", now) for _ in range(import_pages.MAX_PENDING + 5)]
    assert len(store._items) == import_pages.MAX_PENDING
    assert store.take(tokens[-1], "Maya Chen", now) == b"x"


def test_confirm_checks_the_rows_again(mclient, db_path, monkeypatch):
    # What was previewed is not trusted: a row that has become a duplicate by confirm time is skipped.
    token = token_of(upload(mclient, book([good(Title="Race")])))
    other = token_of(upload(mclient, book([good(Title="Race")])))
    assert confirm(mclient, token).headers["location"] == "/mine?imported=1&skipped=0&failed=0"
    assert confirm(mclient, other).headers["location"] == "/mine?imported=0&skipped=1&failed=0"
    assert count(db_path, "SELECT count(*) FROM submission WHERE title = 'Race'") == 1


def test_capacity_stops_the_import_and_says_so(mclient, db_path, monkeypatch):
    from app import submit

    monkeypatch.setattr(submit, "MAX_SUBMISSIONS", 16)
    r = upload(mclient, book([good(Title="T%d" % i) for i in range(5)]))
    done = confirm(mclient, token_of(r))
    assert done.headers["location"] == "/mine?imported=2&skipped=0&failed=0&full=1"
    assert "demo is full" in mclient.get(done.headers["location"]).text


# ---- who may import ----

@pytest.mark.parametrize("path, kwargs", [
    ("/import/preview", {"data": {"source": "sample"}}),
    ("/import/confirm", {"data": {"token": "x"}}),
])
def test_reviewer_is_refused(client, db_path, path, kwargs):
    r = client.post(path, headers=ORIGIN, **kwargs)
    assert r.status_code == 403 and "Only marketers and affiliate partners" in r.text and count(db_path) == 14


@pytest.mark.parametrize("path, kwargs", [
    ("/import/preview", {"data": {"source": "sample"}}),
    ("/import/confirm", {"data": {"token": "x"}}),
])
def test_cross_site_posts_are_refused(mclient, db_path, path, kwargs):
    r = mclient.post(path, headers={"origin": "http://evil.example"}, **kwargs)
    assert r.status_code == 403 and count(db_path) == 14


@pytest.mark.parametrize("method, path", [("GET", "/import/preview"), ("GET", "/import/confirm"),
                                          ("PUT", "/import"), ("POST", "/import"), ("DELETE", "/import/preview"),
                                          ("POST", "/import/sample.xlsx")])
def test_wrong_methods_are_405(mclient, method, path):
    assert mclient.request(method, path).status_code == 405


def test_an_affiliate_imports_only_for_their_own_channel(aclient, db_path):
    head = ["Title", "Product", "Launch date", "Copy"]  # no Channel column needed
    data = book([["Partner page", "Loan", future(20), COPY]], head=head)
    r = upload(aclient, data)
    assert "1 ready" in r.text
    confirm(aclient, token_of(r))
    c = sqlite3.connect(str(db_path))
    row = c.execute("SELECT channel, submitted_by FROM submission WHERE title = 'Partner page'").fetchone()
    c.close()
    assert row == ("affiliate_page", "Northwind Referrals")


def test_an_affiliate_cannot_pick_another_channel_in_the_sheet(aclient, db_path):
    confirm(aclient, token_of(upload(aclient, book([good(Channel="Email")]))))
    assert count(db_path, "SELECT channel FROM submission WHERE submitted_by = 'Northwind Referrals'"
                          " ORDER BY id DESC LIMIT 1") == "affiliate_page"


# ---- the mine banner ----

@pytest.mark.parametrize("query", [
    "imported=abc", "imported=-1", "imported=1&imported=2", "imported=99999", "imported=1.5", "imported=%3Cb%3E",
])
def test_import_banner_only_ever_prints_numbers(mclient, query):
    r = mclient.get("/mine?" + query)
    assert r.status_code == 200 and "<b>" not in r.text and "99999" not in r.text
    assert "Imported" not in r.text or re.search(r"Imported \d+ submission", r.text)


def test_import_banner_clamps_and_reads_skipped_and_failed(mclient):
    r = mclient.get("/mine?imported=3&skipped=1&failed=2&full=1")
    assert "Imported 3 submissions." in r.text and "1 already existed and was skipped" in r.text
    assert "2 rows had errors and were not imported" in r.text and "demo is full" in r.text


# ---- templates ----

def test_new_templates_never_use_safe():
    from pathlib import Path

    for name in ("import.html",):
        assert "|safe" not in (Path(__file__).resolve().parents[1] / "app" / "templates" / name).read_text()
