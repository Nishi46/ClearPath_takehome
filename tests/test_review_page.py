import re

import pytest

from app import db


def test_review_page_for_item_3(client):
    r = client.get("/review/3")
    assert r.status_code == 200
    assert "Mortgage prequal landing page" in r.text
    assert len(re.findall(r"<h1[ >]", r.text)) == 1
    assert re.search(r"<title>[^<]*Mortgage prequal landing page", r.text)
    assert "Back to the queue" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_older_version_is_viewable(client):
    def meta(path):
        r = client.get(path)
        assert r.status_code == 200
        return re.search(r'<p class="review-meta">(.*?)</p>', r.text, re.S).group(1)
    assert "v1" in meta("/review/5?v=1") and "(current)" not in meta("/review/5?v=1")
    assert "v2 (current)" in meta("/review/5")


@pytest.mark.parametrize("path", [
    "/review/abc", "/review/-1", "/review/0", "/review/9999", "/review/1.5", "/review/%E2%80%AE3",
    "/review/1234567890", "/review/3?v=7", "/review/3?v=abc", "/review/3?v=0", "/review/3?v=",
    "/review/3?v=1&v=2", "/review/%00", "/review/1%20OR%201=1",
])
def test_bad_or_unknown_is_the_standard_404_page(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "Page not found" in r.text
    assert "OR" not in r.text and "abc" not in r.text.replace("abcdefghijklmnopqrstuvwxyz", "")
    assert "detail" not in r.text and "Traceback" not in r.text


def test_title_is_escaped_everywhere(client):
    evil = "<script>alert(1)</script>"
    with db.connect() as c:
        c.execute("UPDATE submission SET title = ? WHERE id = 3", (evil,))
    r = client.get("/review/3")
    assert evil not in r.text
    assert r.text.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 2  # <title> and <h1>


@pytest.mark.parametrize("role", ["reviewer", "marketer", "bogus"])
def test_every_role_can_view(client, role):
    client.cookies.set("role", role)
    assert client.get("/review/3").status_code == 200


def test_get_changes_nothing(client):
    def snapshot():
        with db.connect() as c:
            return [tuple(r) for r in c.execute("SELECT id, status FROM submission ORDER BY id")]
    before = snapshot()
    client.get("/review/1")
    assert snapshot() == before
    assert client.post("/review/3").status_code in (404, 405)


def test_queue_links_reach_the_page(client):
    for sid in re.findall(r'href="/review/(\d+)"', client.get("/").text):
        assert client.get(f"/review/{sid}").status_code == 200
