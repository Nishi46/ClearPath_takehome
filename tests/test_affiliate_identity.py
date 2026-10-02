import pytest

from app.roles import (AFFILIATES, ALL_SUBMITTERS, DEFAULT_AFFILIATE, MARKETERS, ROLES, get_affiliate,
                       get_role, get_submitter, is_partner)


class FakeRequest:
    def __init__(self, cookies):
        self.cookies = cookies


def page(client, path="/submit", cookie=""):
    r = client.get(path, headers={"Cookie": cookie})
    assert r.status_code == 200
    return r.text


def test_roles_include_affiliate_and_names_never_overlap():
    assert ROLES == ("reviewer", "marketer", "affiliate")
    assert set(MARKETERS).isdisjoint(AFFILIATES)
    assert ALL_SUBMITTERS == MARKETERS + AFFILIATES


@pytest.mark.parametrize("value", ["", "Affiliate", " affiliate", "admin", "x" * 5000, "partner"])
def test_unknown_role_values_fall_back_to_reviewer(value):
    assert get_role(FakeRequest({"role": value})) == "reviewer"


@pytest.mark.parametrize("value", ["", "northwind referrals", "Northwind Referrals ", "Maya Chen", "Jordan Lee",
                                   "x" * 5000, "Nörthwind Referrals"])
def test_unknown_affiliate_values_fall_back_to_the_default(value):
    assert get_affiliate(FakeRequest({"affiliate": value})) == DEFAULT_AFFILIATE


def test_a_marketer_name_in_the_affiliate_cookie_is_not_accepted(client):
    html = page(client, cookie="role=affiliate; affiliate=Maya Chen")
    assert "Submitting as <strong>%s</strong>" % DEFAULT_AFFILIATE in html


def test_get_submitter_follows_the_role_and_ignores_the_other_cookie():
    both = {"marketer": "Jordan Lee", "affiliate": "BlueLeaf Media"}
    assert get_submitter(FakeRequest(dict(both, role="marketer"))) == "Jordan Lee"
    assert get_submitter(FakeRequest(dict(both, role="affiliate"))) == "BlueLeaf Media"
    assert get_submitter(FakeRequest(dict(both, role="reviewer"))) == "Jordan Lee"


def test_is_partner():
    assert is_partner("BlueLeaf Media") and not is_partner("Maya Chen")
    assert not is_partner(None) and not is_partner(5) and not is_partner("blueleaf media")


def test_post_role_affiliate_sets_cookie_and_goes_to_mine(client):
    r = client.post("/role", data={"role": "affiliate"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mine"
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("role=affiliate") and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie


def test_post_role_affiliate_is_secure_over_https(client):
    r = client.post("/role", data={"role": "affiliate"}, headers={"x-forwarded-proto": "https"},
                    follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


def test_role_switcher_shows_three_buttons_with_one_current(client):
    html = page(client, cookie="role=affiliate")
    assert html.count('class="role-option"') >= 3
    assert html.count('aria-current="true"') == 1
    assert 'value="affiliate" class="role-option"\n                  aria-current="true"' in html


@pytest.mark.parametrize("name", AFFILIATES)
def test_each_partner_round_trips(client, name):
    r = client.post("/affiliate", data={"name": name}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mine"
    client.cookies.set("role", "affiliate")
    assert "Submitting as <strong>%s</strong>" % name in client.get("/submit").text


@pytest.mark.parametrize("data", [{"name": "Mallory"}, {}, {"name": ""}, {"name": "blueleaf media"},
                                  {"name": "Maya Chen"}])
def test_bad_affiliate_posts_are_refused_without_cookie_or_echo(client, data):
    r = client.post("/affiliate", data=data, follow_redirects=False)
    assert r.status_code == 400 and "Unknown affiliate partner." in r.text
    assert "affiliate=" not in r.headers.get("set-cookie", "")
    assert "Mallory" not in r.text


def test_affiliate_repeated_field_json_and_crlf_are_refused(client):
    r = client.post("/affiliate", content=b"name=BlueLeaf+Media&name=Summit+Savers",
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and "set-cookie" not in r.headers
    r = client.post("/affiliate", json={"name": "BlueLeaf Media"})
    assert r.status_code == 400 and "set-cookie" not in r.headers
    r = client.post("/affiliate", data={"name": "BlueLeaf Media\r\nSet-Cookie: x=1"})
    assert r.status_code == 400 and "x=1" not in r.headers.get("set-cookie", "")


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_affiliate_cross_origin_is_refused(client, origin):
    r = client.post("/affiliate", data={"name": "BlueLeaf Media"}, headers={"Origin": origin},
                    follow_redirects=False)
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_affiliate_matching_origin_works(client):
    r = client.post("/affiliate", data={"name": "BlueLeaf Media"}, headers={"Origin": "http://testserver"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_affiliate_route_rejects_get_and_big_bodies(client):
    assert client.get("/affiliate").status_code == 405
    r = client.post("/affiliate", content=b"name=" + b"x" * 2_000_000,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


@pytest.mark.parametrize("role,expected", [("reviewer", False), ("marketer", True), ("affiliate", True)])
def test_nav_shows_my_submissions_for_submitting_roles_only(client, role, expected):
    html = page(client, "/", cookie="role=" + role)
    assert ("href=\"/mine\">My submissions" in html) is expected
    assert 'href="/submit"' in html


def test_viewing_as_lists_only_marketers_for_reviewers_and_marketers(client):
    for role in ("reviewer", "marketer"):
        html = page(client, "/mine", cookie="role=" + role)
        assert 'action="/marketer"' in html and 'action="/affiliate"' not in html
        assert not any('value="%s"' % name in html for name in AFFILIATES)


def test_viewing_as_lists_only_partners_for_affiliates(client):
    html = page(client, "/mine", cookie="role=affiliate")
    assert 'action="/affiliate"' in html and 'action="/marketer"' not in html
    assert all('value="%s"' % name in html for name in AFFILIATES)
    assert html.count('aria-current="true"') == 2  # the role button and the partner


def test_picking_a_person_never_changes_the_role_cookie(client):
    for path, name in (("/affiliate", "BlueLeaf Media"), ("/marketer", "Jordan Lee")):
        r = client.post(path, data={"name": name}, headers={"Cookie": "role=marketer"}, follow_redirects=False)
        assert "role=" not in " | ".join(r.headers.get_list("set-cookie"))
