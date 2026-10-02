import pytest

from app import db
from app.roles import DEFAULT_MARKETER, MARKETERS


def who(client, cookie=None):
    r = client.get("/submit", headers={"Cookie": "role=marketer" + ("; " + cookie if cookie else "")})
    assert r.status_code == 200
    return r.text


def test_no_cookie_is_the_default_marketer(client):
    assert "Submitting as <strong>Maya Chen</strong>" in who(client)


@pytest.mark.parametrize("name", MARKETERS)
def test_each_allowed_name_round_trips(client, name):
    r = client.post("/marketer", data={"name": name}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mine"
    client.cookies.set("role", "marketer")
    assert "Submitting as <strong>%s</strong>" % name in client.get("/submit").text


@pytest.mark.parametrize("value", ["", "maya chen", "Maya Chen ", "Admin", "Maya%20Chen%0D%0ASet-Cookie:%20x=1",
                                   "x" * 5000])
def test_unknown_cookie_values_fall_back_to_the_default(client, value):
    text = who(client, "marketer=%s" % value)
    assert "Submitting as <strong>%s</strong>" % DEFAULT_MARKETER in text


def test_repeated_cookie_does_not_error(client):
    r = client.get("/submit", headers={"Cookie": "role=marketer; marketer=Jordan Lee; marketer=Sam Patel"})
    assert r.status_code == 200 and "Submitting as" in r.text


@pytest.mark.parametrize("data", [{"name": "Mallory"}, {}, {"name": ""}, {"name": "maya chen"}])
def test_bad_posts_are_refused_without_a_cookie_or_an_echo(client, data):
    r = client.post("/marketer", data=data, follow_redirects=False)
    assert r.status_code == 400 and r.text == "Unknown marketer."
    assert "marketer" not in r.headers.get("set-cookie", "")
    assert "Mallory" not in r.text


def test_repeated_field_and_json_body_are_refused(client):
    r = client.post("/marketer", content=b"name=Maya+Chen&name=Jordan+Lee",
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and "set-cookie" not in r.headers
    r = client.post("/marketer", json={"name": "Maya Chen"})
    assert r.status_code == 400 and "set-cookie" not in r.headers


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_cross_origin_is_refused(client, origin):
    r = client.post("/marketer", data={"name": "Jordan Lee"}, headers={"Origin": origin}, follow_redirects=False)
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_matching_origin_works(client):
    r = client.post("/marketer", data={"name": "Jordan Lee"}, headers={"Origin": "http://testserver"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_cookie_flags(client):
    low = client.post("/marketer", data={"name": "Jordan Lee"}, follow_redirects=False).headers["set-cookie"].lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low and "secure" not in low
    secure = client.post("/marketer", data={"name": "Jordan Lee"}, headers={"X-Forwarded-Proto": "https"},
                         follow_redirects=False).headers["set-cookie"].lower()
    assert "secure" in secure


def test_the_name_is_never_reflected_into_a_header(client):
    r = client.post("/marketer", data={"name": "Maya Chen\r\nX-Evil: 1"}, follow_redirects=False)
    assert r.status_code == 400 and "x-evil" not in r.headers


def test_identity_changes_nothing_in_the_queue(client):
    before = client.get("/").text
    client.post("/marketer", data={"name": "Sam Patel"})
    assert client.get("/").text == before


def test_picking_a_marketer_writes_nothing(client):
    client.post("/marketer", data={"name": "Jordan Lee"})
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM submission").fetchone()[0] == 14
