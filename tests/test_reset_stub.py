import re


def test_reset_button_is_disabled_with_title(client):
    html = client.get("/").text
    button = re.search(r"<button[^>]*>Reset demo</button>", html).group(0)
    assert re.search(r"\bdisabled\b", button)
    assert 'type="button"' in button  # never submits a form
    assert "phase 2" in re.search(r'title="([^"]*)"', button).group(1)


def test_reset_warning_copy_is_visible_and_linked(client):
    html = client.get("/").text
    assert "Resets data for everyone using this demo." in html
    assert 'aria-describedby="reset-demo-note"' in html
    assert 'id="reset-demo-note"' in html


def test_reset_button_is_not_inside_a_form(client):
    html = client.get("/").text
    for form in re.findall(r"<form.*?</form>", html, re.S):
        assert "Reset demo" not in form


def test_no_reset_route_exists_yet(client):
    for method in ("GET", "POST", "PUT", "DELETE"):
        assert client.request(method, "/reset").status_code in (404, 405)
    assert client.post("/reset", data={"confirm": "yes"}).status_code in (404, 405)


def test_no_route_in_the_app_mentions_reset():
    from app.main import app

    assert not any("reset" in getattr(r, "path", "") for r in app.routes)
