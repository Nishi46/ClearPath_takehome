import re
import sqlite3

import pytest

from app.security import CSP

EXPECTED_ORDER = [11, 1, 2, 14, 3, 4, 5, 13, 6, 7, 8, 9, 10, 12]
HEADERS = ["Title", "Product", "Channel", "Launch date (earliest first)", "Status", "Submitter", "Flags", "Version"]


def body_rows(html):
    tbody = re.search(r"<tbody>(.*?)</tbody>", html, re.S).group(1)
    return re.findall(r"<tr\b.*?</tr>", tbody, re.S)


def row_for(html, sid):
    return next(r for r in body_rows(html) if f'href="/review/{sid}"' in r)


def add_submission(db_path, sid, title, **kw):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT INTO submission (id, title, product, channel, status, launch_date, submitted_by, created_at,"
              " current_version) VALUES (?, ?, 'loan', 'email', 'new', ?, ?, '2026-01-01T00:00:00Z', 1)",
              (sid, title, kw.get("launch", "2099-01-01"), kw.get("by", "Maya Chen")))
    c.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (?, 1, 'c', 'x')", (sid,))
    c.commit()
    c.close()


# ---- structure ----

def test_fresh_app_renders_14_rows_and_14_links(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert len(body_rows(html)) == 14
    assert len(re.findall(r'<a href="/review/\d+">', html)) == 14


def test_rows_are_in_launch_order(client):
    ids = [int(i) for i in re.findall(r'<a href="/review/(\d+)">', client.get("/").text)]
    assert ids == EXPECTED_ORDER


def test_eight_column_headers_with_scope(client):
    html = client.get("/").text
    heads = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', html)
    assert heads == HEADERS
    assert 'aria-sort="ascending"' in html


def test_table_has_a_caption_and_real_table_markup(client):
    html = client.get("/").text
    assert re.search(r"<caption[^>]*>.+?</caption>", html)
    assert html.count("<table") == 1 and "<thead>" in html and "<tbody>" in html


def test_every_cell_has_a_label_for_the_narrow_layout(client):
    row = body_rows(client.get("/").text)[0]
    labels = re.findall(r'<td data-label="([^"]+)"', row)
    assert labels == ["Title", "Product", "Channel", "Launch date", "Status", "Submitter", "Flags", "Version"]


# ---- content ----

def test_first_row_is_the_overdue_item(client):
    first = body_rows(client.get("/").text)[0]
    assert "Spring loan promo" in first
    assert "Overdue by 1 day" in first
    assert "urgency-overdue" in first


def test_rush_rows(client):
    html = client.get("/").text
    assert "Launches tomorrow" in row_for(html, 1)
    assert "urgency-rush" in row_for(html, 1) and "urgency-rush" in row_for(html, 2)
    assert "Rush" in row_for(html, 2) or "Launches" in row_for(html, 2)


def test_non_urgent_and_final_rows_have_no_urgency(client):
    html = client.get("/").text
    for sid in (10, 12, 6, 7, 8, 13):
        row = row_for(html, sid)
        assert "urgency" not in row, sid


def test_row_text_values(client):
    row = row_for(client.get("/").text, 5)
    for text in ("Balance transfer email", "Card", "Email", "In review", "Maya Chen"):
        assert text in row
    assert re.search(r'<time datetime="\d{4}-\d{2}-\d{2}">[A-Z][a-z]{2} \d{1,2}, \d{4}</time>', row)


def test_status_and_urgency_are_words_in_the_markup_not_only_classes(client):
    html = client.get("/").text
    for word in ("New", "In review", "Changes requested", "Approved", "Rejected", "Overdue by"):
        assert word in html
    shown = re.sub(r'class="[^"]*"', "", "".join(body_rows(html)))
    assert "in_review" not in shown and "changes_requested" not in shown   # raw words only inside class names


def test_flags_show_the_live_count(client):
    row = row_for(client.get("/").text, 1)
    assert re.search(r'<td data-label="Flags"><span>3</span></td>', row)
    assert "phase 3" not in client.get("/").text


def test_reset_banner_still_works_on_the_queue(client):
    assert "Demo reset to the original data." in client.get("/?reset=done").text
    assert "Demo reset to the original data." not in client.get("/").text


def test_both_roles_see_the_same_queue(client):
    client.cookies.set("role", "marketer")
    assert len(body_rows(client.get("/").text)) == 14


# ---- escaping ----

@pytest.mark.parametrize("title", [
    "<img src=x onerror=alert(1)>",
    '"><script>alert(1)</script>',
    "Tom & Jerry's <b>bold</b>",
    "</td></tr></table><h1>injected</h1>",
    "{{ 7*7 }} {% raw %}",
])
def test_hostile_titles_are_escaped_in_link_text(client, db_path, title):
    add_submission(db_path, 101, title)
    html = client.get("/").text
    row = row_for(html, 101)
    assert "<img src=x" not in html and "<script>alert" not in html
    assert "<h1>injected</h1>" not in html and "<b>bold</b>" not in html
    assert "49" not in row                      # template syntax is not evaluated
    assert 'href="/review/101"' in row         # the link target never comes from the title
    assert len(body_rows(html)) == 15           # the row structure survived


def test_escaped_output_is_visible_as_text(client, db_path):
    add_submission(db_path, 101, "<img src=x onerror=alert(1)>")
    assert "&lt;img src=x onerror=alert(1)&gt;" in client.get("/").text


def test_hostile_submitter_name_is_escaped(client, db_path):
    add_submission(db_path, 101, "ok", by="<script>alert(2)</script>")
    html = client.get("/").text
    assert "<script>alert(2)" not in html and "&lt;script&gt;alert(2)" in html


def test_long_and_unusual_titles_do_not_break_the_page(client, db_path):
    add_submission(db_path, 101, "A" * 500)
    add_submission(db_path, 102, "Emoji \U0001F600 \u202E rtl שלום 日本語")
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "A" * 500 in row_for(html, 101)
    assert "\U0001F600" in row_for(html, 102)
    assert len(body_rows(html)) == 16


def test_bad_launch_date_in_the_database_does_not_break_the_page(client, db_path):
    add_submission(db_path, 101, "odd", launch="<b>soon</b>")
    html = client.get("/").text
    assert "<b>soon</b>" not in html and "&lt;b&gt;soon&lt;/b&gt;" in html


# ---- security and headers ----

def test_no_inline_styles_or_inline_scripts(client):
    html = client.get("/").text
    assert not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son\w+\s*=", html)   # no inline event handlers


def test_response_headers(client):
    r = client.get("/")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"


def test_review_links_lead_to_the_friendly_404_until_phase_4(client):
    r = client.get("/review/1")
    assert r.status_code == 404 and "Page not found" in r.text


def test_queue_reflects_the_database_on_every_request(client, db_path):
    assert len(body_rows(client.get("/").text)) == 14
    add_submission(db_path, 101, "Brand new")
    assert len(body_rows(client.get("/").text)) == 15


def test_page_after_a_reset_is_the_seed_again(client, db_path):
    add_submission(db_path, 101, "Brand new")
    r = client.post("/reset", data={"confirm": "reset"})
    assert r.status_code == 200
    assert len(body_rows(r.text)) == 14 and "Brand new" not in r.text


def test_database_failure_gives_the_friendly_500_without_details(client, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db
    from app.main import app

    def broken():
        raise RuntimeError("secret /Users/x/db.py detail")

    monkeypatch.setattr(db, "connect", broken)
    r = TestClient(app, raise_server_exceptions=False).get("/")
    assert r.status_code == 500 and "Something went wrong" in r.text
    assert "secret" not in r.text and "RuntimeError" not in r.text
