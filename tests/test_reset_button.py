import re


def reset_link(html):
    return re.search(r"<a\b[^>]*>Reset demo</a>", html).group(0)


def test_reset_control_is_an_enabled_link_to_the_confirmation_page(client):
    link = reset_link(client.get("/").text)
    assert 'href="/reset/confirm"' in link
    assert "disabled" not in link


def test_reset_warning_copy_is_visible_and_linked(client):
    html = client.get("/").text
    assert "Resets data for everyone using this demo." in html
    assert 'aria-describedby="reset-demo-note"' in reset_link(html)
    assert 'id="reset-demo-note"' in html


def test_reset_control_is_not_a_button_or_inside_a_form(client):
    html = client.get("/").text
    assert not re.search(r"<button[^>]*>Reset demo", html)
    for form in re.findall(r"<form.*?</form>", html, re.S):
        assert "Reset demo" not in form


def test_header_link_is_on_every_page(client):
    for path in ("/", "/reset/confirm", "/missing"):
        assert 'href="/reset/confirm"' in client.get(path).text


def test_getting_the_link_target_changes_nothing(live_client, db_path):
    import sqlite3
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE submission SET title = 'Changed' WHERE id = 1")
    c.commit()
    c.close()
    assert live_client.get("/reset/confirm").status_code == 200
    c = sqlite3.connect(str(db_path))
    assert c.execute("SELECT title FROM submission WHERE id = 1").fetchone()[0] == "Changed"
    c.close()


# ---- the banner ----

BANNER = "Demo reset to the original data."


def test_banner_shows_only_for_reset_done(client):
    html = client.get("/?reset=done").text
    assert BANNER in html
    assert 'role="status"' in html


def test_no_banner_by_default(client):
    assert BANNER not in client.get("/").text


def test_banner_is_not_triggered_by_other_values_and_never_reflects_them(client):
    for query in ("?reset=<script>alert(1)</script>", "?reset=", "?reset=done&reset=x",
                  "?reset=x&reset=done", "?reset=DONE", "?reset=done%20", "?reset[]=done", "?other=done"):
        html = client.get("/" + query).text
        assert BANNER not in html, query
    payload = client.get("/?reset=<script>alert(1)</script>").text
    assert "alert(1)" not in payload


def test_banner_survives_a_full_reset_round_trip(live_client, db_path):
    r = live_client.post("/reset", data={"confirm": "reset"})
    assert r.status_code == 200
    assert BANNER in r.text


def test_banner_text_is_words_not_only_styling(client):
    html = client.get("/?reset=done").text
    assert re.search(r'<p class="banner" role="status">\s*Demo reset to the original data\.\s*</p>', html)
