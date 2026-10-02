import pytest


@pytest.mark.parametrize("path", ["/static/style.css", "/static/htmx.min.js"])
def test_static_files_must_revalidate(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag")   # revalidation is cheap: unchanged files answer 304


def test_unchanged_file_is_a_304_not_a_full_download(client):
    first = client.get("/static/style.css")
    again = client.get("/static/style.css", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_changed_etag_gets_the_full_file(client):
    r = client.get("/static/style.css", headers={"If-None-Match": '"stale"'})
    assert r.status_code == 200 and "table.queue" in r.text


def test_security_headers_still_present_on_static_files(client):
    r = client.get("/static/style.css")
    assert r.headers["x-content-type-options"] == "nosniff" and "content-security-policy" in r.headers


def test_missing_static_file_is_still_a_404_and_path_traversal_is_refused(client):
    assert client.get("/static/nope.css").status_code == 404
    assert client.get("/static/../app/main.py").status_code == 404
    assert client.get("/static/%2e%2e/app/main.py").status_code == 404


def test_pages_are_not_given_the_static_policy(client):
    assert client.get("/").headers["cache-control"] == "no-store"      # the queue's own header is untouched
    assert client.get("/reset/confirm").headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/missing-page").headers   # error pages get no static policy
