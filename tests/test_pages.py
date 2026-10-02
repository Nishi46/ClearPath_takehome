import re
from pathlib import Path

from app.templating import APP_DIR, templates


def test_home_renders_layout(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for text in ("ClearPath Review", "Submit", "Reset demo", "Review queue"):
        assert text in r.text
    assert 'href="/submit"' in r.text


def test_document_basics(client):
    html = client.get("/").text
    assert html.lower().startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in html
    assert 'name="viewport"' in html
    assert "<title>Queue - ClearPath Review</title>" in html
    assert '<html lang="en">' in html


def test_landmarks_and_skip_link(client):
    html = client.get("/").text
    assert '<a class="skip-link" href="#main">' in html
    assert re.search(r"<header\b", html) and re.search(r"<nav\b", html)
    assert re.search(r'<main id="main"', html)


def test_role_and_state_are_text_not_only_color(client):
    html = client.get("/").text
    assert "Reviewer (current)" in html and "Marketer" in html
    assert 'aria-current="true"' in html


def test_autoescape_is_on():
    assert templates.env.autoescape
    out = templates.env.from_string("{{ x }}").render(x="<script>alert(1)</script>")
    assert "&lt;script&gt;" in out and "<script>" not in out
    assert templates.env.get_template("base.html").environment.autoescape


def test_htmx_is_local_and_has_integrity(client):
    html = client.get("/").text
    tag = re.search(r"<script[^>]*htmx[^>]*>", html).group(0)
    assert 'src="/static/htmx.min.js"' in tag
    assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]+"', tag)
    assert "crossorigin" in tag
    assert "http://" not in html and "https://" not in html  # no third-party loads


def test_no_inline_scripts_or_styles(client):
    html = client.get("/").text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "<style" not in html and ' style="' not in html


def test_static_css_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")


def test_static_js_served(client):
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


def test_static_path_traversal_blocked(client):
    for path in ("/static/../app/main.py", "/static/%2e%2e/app/main.py",
                 "/static/..%2fapp/main.py", "/static/%2e%2e%2fapp%2fmain.py",
                 "/static/....//app/main.py"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "FastAPI" not in r.text, path


def test_static_directory_not_listed(client):
    assert client.get("/static/").status_code == 404


def test_no_cdn_urls_in_templates():
    for f in (APP_DIR / "templates").glob("*.html"):
        assert "http" not in f.read_text(), f
