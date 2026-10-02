import hashlib
import re

import pytest

from app import templating
from app.templating import APP_DIR, asset_url


def href(html):
    return re.search(r'<link rel="stylesheet" href="([^"]+)">', html).group(1)


def test_page_links_a_versioned_stylesheet(client):
    assert re.fullmatch(r"/static/style\.css\?v=[0-9a-f]{10}", href(client.get("/").text))


def test_the_versioned_url_serves_the_current_css(client):
    r = client.get(href(client.get("/").text))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    assert "table.queue" in r.text and ".urgency-overdue" in r.text


def test_version_is_a_hash_of_the_file_contents():
    expected = hashlib.sha256((APP_DIR / "static" / "style.css").read_bytes()).hexdigest()[:10]
    assert asset_url("style.css") == "/static/style.css?v=" + expected


def test_version_is_stable_between_calls_and_pages(client):
    assert asset_url("style.css") == asset_url("style.css")
    assert href(client.get("/").text) == href(client.get("/reset/confirm").text) == href(client.get("/nope").text)


def test_changing_the_css_changes_the_url(tmp_path, monkeypatch):
    (tmp_path / "style.css").write_text("body { color: red; }")
    monkeypatch.setattr(templating, "_STATIC", tmp_path)
    first = asset_url("style.css")
    (tmp_path / "style.css").write_text("body { color: blue; }")   # same size, new content
    second = asset_url("style.css")
    assert first != second
    (tmp_path / "style.css").write_text("body { color: red; }")
    assert asset_url("style.css") == first                        # back to the old contents, back to the old URL


def test_old_unversioned_url_still_works(client):
    assert client.get("/static/style.css").status_code == 200


def test_query_string_cannot_be_used_to_read_other_files(client):
    assert client.get("/static/style.css?v=../../app/main.py").text.lstrip().startswith(":root")
    assert client.get("/static/../app/main.py?v=1").status_code == 404


@pytest.mark.parametrize("bad", ["../app/main.py", "a/b.css", "..", ".env", "..\\x", "/etc/passwd", ""])
def test_asset_names_must_be_plain_file_names(bad):
    with pytest.raises((ValueError, OSError)):
        asset_url(bad)


def test_missing_file_fails_loudly_rather_than_linking_a_dead_url():
    with pytest.raises(OSError):
        asset_url("not-there.css")


def test_htmx_script_keeps_its_integrity_hash_and_it_is_correct(client):
    import base64
    tag = re.search(r"<script[^>]*htmx[^>]*>", client.get("/").text).group(0)
    claimed = re.search(r'integrity="sha384-([^"]+)"', tag).group(1)
    actual = base64.b64encode(hashlib.sha384((APP_DIR / "static" / "htmx.min.js").read_bytes()).digest()).decode()
    assert claimed == actual   # a mismatch would make the browser refuse the script


def test_error_pages_use_the_versioned_stylesheet_too(client):
    r = client.get("/definitely-missing")
    assert r.status_code == 404 and "?v=" in href(r.text)
