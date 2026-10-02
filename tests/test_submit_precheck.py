import time

import pytest

from app import db, rules, submit


def seed_item(sid):
    with db.connect() as c:
        row = c.execute("SELECT s.product, s.channel, v.copy, v.id AS vid FROM submission s JOIN version v"
                        " ON v.submission_id = s.id AND v.version_number = s.current_version WHERE s.id = ?",
                        (sid,)).fetchone()
        stored = [tuple(r) for r in c.execute(
            "SELECT rule_id, severity, kind FROM flag WHERE version_id = ? ORDER BY rule_id, id", (row["vid"],))]
    return dict(row), stored


def card_summary(check):
    return sorted({(c["rule_id"], c["severity"], c["kind"]) for c in check["cards"]})


def test_item_1_matches_the_flags_the_reviewer_sees(client):
    item, stored = seed_item(1)
    check = submit.precheck(item["product"], item["channel"], item["copy"])
    assert check["state"] == "ok"
    assert {c["rule_id"] for c in check["cards"]} == {"R1", "R2", "R5"}
    assert card_summary(check) == sorted(set(stored))


@pytest.mark.parametrize("sid", range(1, 15))
def test_every_seed_item_agrees_with_its_stored_flags(client, sid):
    item, stored = seed_item(sid)
    check = submit.precheck(item["product"], item["channel"], item["copy"])
    assert card_summary(check) == sorted(set(stored))


def test_a_clean_copy_has_no_flags(client):
    item, _ = seed_item(6)
    check = submit.precheck(item["product"], item["channel"], item["copy"])
    assert check == {"state": "ok", "cards": []}


def test_missing_cards_carry_the_snippet_and_phrase_cards_the_matched_text(client):
    check = submit.precheck("loan", "email", "Guaranteed approval!")
    by_rule = {c["rule_id"]: c for c in check["cards"]}
    assert by_rule["R1"]["occurrences"][0]["full"] == "Guaranteed approval"
    assert by_rule["R5"]["kind"] == "missing" and by_rule["R5"]["snippet"]


def test_scope_changes_the_flags():
    copy = "Guaranteed approval, rates as low as 5.99%."
    loan = card_summary(submit.precheck("loan", "email", copy))
    mortgage = card_summary(submit.precheck("mortgage", "display", copy))
    assert loan != mortgage


@pytest.mark.parametrize("args", [
    (None, "email", "x"), ("loan", None, "x"), ("credit", "email", "x"), ("loan", "fax", "x"), ("loan", "email", ""),
    ("loan", "email", "   \n"), ("loan", "email", None), (["loan"], "email", "x"), ("loan", ["email"], "x"),
    ("loan", "email", ["x"]), ("loan", "email", "​")])
def test_incomplete_input_gives_a_friendly_message_not_an_error(args):
    check = submit.precheck(*args)
    assert check["state"] == "incomplete" and check["message"]


def test_too_long_and_control_characters_are_named():
    assert submit.precheck("loan", "email", "a" * 10_001)["message"] == submit.MESSAGES["copy_long"]
    assert submit.precheck("loan", "email", "a\x00b")["message"] == submit.MESSAGES["copy_chars"]


def test_crlf_gives_the_same_flags_as_lf():
    lf = submit.precheck("loan", "email", "Hello.\nGuaranteed approval.")
    crlf = submit.precheck("loan", "email", "Hello.\r\nGuaranteed approval.")
    assert lf == crlf


def test_hostile_copy_is_quick():
    for copy in ("<script>alert(1)</script> " * 300, "approved " * 1100, "!!!???...," * 1000, "a" * 10_000):
        start = time.perf_counter()
        assert submit.precheck("loan", "email", copy)["state"] == "ok"
        assert time.perf_counter() - start < 2


# ---- the route ----

def check(client, data, headers=None):
    return client.post("/submit/check", data=data, headers=headers or {})


def counts():
    with db.connect() as c:
        return [c.execute("SELECT count(*) FROM %s" % t).fetchone()[0] for t in ("submission", "version", "flag")]


def test_full_page_check_shows_the_flags_and_keeps_the_input(mclient):
    r = check(mclient, {"product": "loan", "channel": "email", "copy": "Guaranteed approval!", "title": "My title"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "R1: " in r.text and "Missing: add this" in r.text and 'value="My title"' in r.text
    assert "Guaranteed approval!</textarea>" in r.text


def test_fragment_has_the_notes_and_no_page_chrome(mclient):
    r = check(mclient, {"product": "loan", "channel": "email", "copy": "Hello"}, {"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text and "<form" not in r.text
    assert "Flags assist the reviewer. They never decide." in r.text
    assert "Rules are illustrative, not legal advice." in r.text
    assert "Flags don't block submitting. Reviewers will see them." in r.text
    assert 'id="launch-warning"' in r.text and 'hx-swap-oob="true"' in r.text


def test_clean_copy_says_no_flags_detected_and_not_a_guarantee(mclient):
    item, _ = seed_item(6)
    r = check(mclient, {"product": item["product"], "channel": item["channel"], "copy": item["copy"]},
              {"HX-Request": "true"})
    assert "No flags detected." in r.text and "not a guarantee of compliance" in r.text


@pytest.mark.parametrize("data", [
    {}, {"product": "loan"}, {"product": "loan", "channel": "email"}, {"product": "loan", "channel": "email", "copy": "  "},
    {"product": "bad", "channel": "email", "copy": "x"}, {"product": "loan", "channel": "email", "copy": "a" * 10_001}])
def test_incomplete_input_is_a_200_with_a_message(mclient, data):
    for headers in ({}, {"HX-Request": "true"}):
        r = check(mclient, data, headers)
        assert r.status_code == 200 and "flag-empty" in r.text


def test_repeated_fields_and_wrong_content_types_never_error(mclient):
    r = mclient.post("/submit/check", content=b"product=loan&product=card&channel=email&copy=x",
                     headers={"content-type": "application/x-www-form-urlencoded", "HX-Request": "true"})
    assert r.status_code == 200 and "Choose a product" in r.text
    assert mclient.post("/submit/check", json={"copy": "x"}).status_code == 200


def test_nothing_is_written(mclient):
    before = counts()
    check(mclient, {"product": "loan", "channel": "email", "copy": "Guaranteed approval!", "title": "T",
                    "launch_date": "2026-10-08"})
    assert counts() == before


def test_hostile_copy_is_escaped(mclient):
    page = check(mclient, {"product": "loan", "channel": "email", "title": "\"><img src=x onerror=alert(1)>",
                           "copy": "<script>alert(1)</script> Guaranteed approval"})
    assert "<script>alert(1)" not in page.text and "<img src=x" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Guaranteed approval" in page.text
    fragment = check(mclient, {"product": "loan", "channel": "email", "copy": "<b>Guaranteed approval</b>"},
                     {"HX-Request": "true"})
    assert "<b>" not in fragment.text


def test_reviewer_role_is_refused(client):
    client.cookies.set("role", "reviewer")
    assert check(client, {"product": "loan"}).status_code == 403
    assert client.post("/submit/check", data={"product": "loan"}).text.count("Only marketers and affiliate partners can submit.") == 1


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_cross_origin_is_refused(mclient, origin):
    assert check(mclient, {"product": "loan"}, {"Origin": origin}).status_code == 403


def test_get_is_not_allowed(mclient):
    assert mclient.get("/submit/check").status_code == 405


def test_oversized_body_is_refused(mclient):
    r = mclient.post("/submit/check", data={"copy": "a" * (2 * 1024 * 1024)})
    assert r.status_code == 413


def test_the_check_route_never_opens_the_database(mclient, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the database was opened")
    monkeypatch.setattr(db, "connect", boom)
    r = check(mclient, {"product": "loan", "channel": "email", "copy": "Hello"}, {"HX-Request": "true"})
    assert r.status_code == 200
