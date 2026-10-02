import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from html.parser import HTMLParser

from datetime import timedelta

from app import clock, db

LAUNCH = (clock.today() + timedelta(days=30)).isoformat()

TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")
NEW_COPY = "Apply today. Rates from 5.99% APR. Subject to credit approval."
HOSTILE = '"><img src=x onerror=alert(1)></textarea><script>alert(1)</script>'


def as_marketer(client, name):
    client.cookies.set("role", "marketer")
    client.cookies.set("marketer", name)
    return client


@pytest.fixture
def jordan(client):
    return as_marketer(client, "Jordan Lee")


def dump():
    with db.connect() as c:
        return {t: [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)] for t in TABLES}


def stored_copy(sid, number):
    with db.connect() as c:
        return c.execute("SELECT copy FROM version WHERE submission_id = ? AND version_number = ?",
                         (sid, number)).fetchone()[0]


def form(**over):
    data = {"copy": NEW_COPY, "notes": "Fixed.", "launch_date": LAUNCH, "base_version": "1"}
    data.update(over)
    return data


def post(client, sid=14, data=None, headers=None):
    return client.post("/resubmit/%s" % sid, data=form() if data is None else data, headers=headers or {},
                       follow_redirects=False)


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags(html, name):
    p = Tags()
    p.feed(html)
    return [a for t, a in p.tags if t == name]


# ---- GET ----

def test_14_as_jordan_is_prefilled_with_the_stored_copy_and_reason(jordan):
    r = jordan.get("/resubmit/14")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    from html import unescape
    area = re.search(r'<textarea id="field-copy"[^>]*>\n(.*?)</textarea>', r.text, re.S).group(1)
    assert unescape(area) == stored_copy(14, 1)
    assert 'name="base_version" value="1"' in r.text
    assert "Alex Rivera" in r.text and "asked for changes" in r.text
    assert len(tags(r.text, "textarea")) == 2  # copy and notes
    # title, product and channel are text, not inputs
    assert not [a for a in tags(r.text, "input") if a.get("name") in ("title", "product", "channel")]
    assert not tags(r.text, "select")
    assert "Pre-check" in r.text and "Compare with previous" in r.text


def test_8_rejected_shows_the_rejection(jordan):
    r = jordan.get("/resubmit/8")
    assert r.status_code == 200 and "rejected this version" in r.text and "Resubmit for review" in r.text


@pytest.mark.parametrize("sid,who,text", [
    (6, "Maya Chen", "approved and locked"), (7, "Jordan Lee", "approved and locked"),
    (3, "Maya Chen", "still in review"), (1, "Maya Chen", "still in review"),
    (14, "Maya Chen", "belongs to Jordan Lee")])
def test_no_form_when_not_resubmittable(client, sid, who, text):
    as_marketer(client, who)
    r = client.get("/resubmit/%d" % sid)
    assert r.status_code == 200 and text in r.text
    assert "<form method=\"post\" action=\"/resubmit" not in r.text and not tags(r.text, "textarea")


def test_reviewer_gets_an_explanation(client):
    r = client.get("/resubmit/14")
    assert r.status_code == 200 and "Only marketers and affiliate partners resubmit" in r.text and not tags(r.text, "textarea")


@pytest.mark.parametrize("bad", ["abc", "-1", "0", "9999", "1.5", "%E2%80%AE3", "1234567890"])
def test_bad_ids_are_404_without_echo(jordan, bad):
    r = jordan.get("/resubmit/" + bad)
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    assert bad not in r.text or bad.isdigit()


def test_hostile_copy_cannot_break_out_of_the_textarea(jordan):
    assert post(jordan, data=form(copy=HOSTILE)).status_code == 303
    # now resubmittable again only after a decision; check the 422 re-render path instead
    r = post(jordan, 8, form(copy=HOSTILE, launch_date="nope"))
    assert r.status_code == 422
    assert "<script>" not in r.text and "<img" not in r.text
    assert len(tags(r.text, "textarea")) == 2


# ---- POST ----

def test_happy_path_for_14(jordan, client):
    before = dump()
    r = post(jordan)
    assert r.status_code == 303 and r.headers["location"] == "/mine?submitted=14"
    after = dump()
    assert len(after["version"]) == len(before["version"]) + 1
    assert stored_copy(14, 2) == NEW_COPY
    with db.connect() as c:
        assert tuple(c.execute("SELECT status, current_version FROM submission WHERE id = 14").fetchone()) == ("in_review", 2)
    assert "Resubmitted as v2." in jordan.get("/mine?submitted=14").text
    assert "In review" in client.get("/").text
    # refreshing the target does not resubmit; reposting the stale form is a 409
    assert post(jordan).status_code == 409
    assert len(dump()["version"]) == len(after["version"])
    client.cookies.set("role", "reviewer")
    page = client.get("/review/14").text
    assert 'name="outcome"' in page


def test_unchanged_copy_is_blocked_with_the_message(jordan):
    before = dump()
    r = post(jordan, data=form(copy=stored_copy(14, 1)))
    assert r.status_code == 422 and "Nothing has changed in the copy" in r.text
    assert "Fix 1 thing below" in r.text
    assert dump() == before


def test_whitespace_and_line_ending_only_changes_are_blocked(jordan):
    before = dump()
    copy = stored_copy(14, 1)
    for variant in ("  " + copy + "\n\n", copy.replace("\n", "\r\n")):
        assert post(jordan, data=form(copy=variant)).status_code == 422
    assert dump() == before


@pytest.mark.parametrize("over", [{"copy": ""}, {"copy": "   "}, {"copy": "x" * 10_001}, {"launch_date": "2026-02-30"},
                                  {"launch_date": ""}, {"notes": "n" * 2_001}])
def test_validation_errors_are_422(jordan, over):
    before = dump()
    r = post(jordan, data=form(**over))
    assert r.status_code == 422 and 'role="alert"' in r.text and "Traceback" not in r.text
    assert dump() == before


def test_validation_keeps_the_input(jordan):
    r = post(jordan, data=form(copy="my edit KEEPME", launch_date="bad"))
    assert r.status_code == 422 and "my edit KEEPME" in r.text and 'name="base_version" value="1"' in r.text


def test_repeated_and_missing_fields_never_500(jordan):
    before = dump()
    r = jordan.post("/resubmit/14", content="copy=a&copy=b&launch_date=" + LAUNCH + "&base_version=1",
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422
    assert jordan.post("/resubmit/14", json={"copy": "x"}).status_code == 422
    assert jordan.post("/resubmit/14", data={}).status_code == 422
    assert dump() == before


def test_missing_or_bad_base_version_is_a_409(jordan):
    before = dump()
    for bad in (None, "", "0", "2", "-1", "x", "1.0"):
        data = form()
        if bad is None:
            del data["base_version"]
        else:
            data["base_version"] = bad
        assert post(jordan, data=data).status_code == 409
    assert dump() == before


def test_forged_fields_are_ignored(jordan):
    r = post(jordan, data=form(product="loan", channel="email", title="Hacked", status="approved",
                               submitted_by="Maya Chen", current_version="9", id="1", outcome="approved",
                               version_number="5"))
    assert r.status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT title, product, channel, submitted_by, status, current_version FROM submission"
                        " WHERE id = 14").fetchone()
        assert row["title"] != "Hacked" and (row["product"], row["channel"]) == ("mortgage", "display")
        assert (row["submitted_by"], row["status"], row["current_version"]) == ("Jordan Lee", "in_review", 2)


def test_another_marketers_item_is_403_and_nothing_is_written(client):
    as_marketer(client, "Maya Chen")
    before = dump()
    assert post(client).status_code == 403
    assert dump() == before


def test_reviewer_is_403(client):
    before = dump()
    assert post(client).status_code == 403
    assert dump() == before


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_cross_origin_is_403(jordan, origin):
    before = dump()
    assert post(jordan, headers={"Origin": origin}).status_code == 403
    assert dump() == before


def test_a_matching_origin_works(jordan):
    assert post(jordan, headers={"Origin": "http://testserver"}).status_code == 303


@pytest.mark.parametrize("sid", [6, 7, 9, 1])
def test_locked_and_in_review_items_are_409(client, sid):
    as_marketer(client, "Maya Chen" if sid in (6, 9, 1) else "Jordan Lee")
    before = dump()
    for over in ({}, {"copy": ""}.copy(), {"base_version": "9"}):
        r = post(client, sid, form(**over))
        assert r.status_code in (409, 422)
    assert post(client, sid).status_code == 409
    assert dump() == before


def test_stale_form_is_409_and_shows_the_real_state(jordan):
    assert post(jordan).status_code == 303          # the other tab
    before = dump()
    r = post(jordan, data=form(copy="Another edit entirely."))
    assert r.status_code == 409 and "changed since you opened the form" in r.text
    assert "still in review" in r.text and not tags(r.text, "textarea")
    assert dump() == before


def test_bad_ids_and_unknown_items(jordan):
    assert post(jordan, "abc").status_code == 404
    assert post(jordan, 9999).status_code == 404


def test_concurrent_double_submit_makes_one_version(jordan, client):
    def go(_):
        return client.post("/resubmit/14", data=form(), follow_redirects=False).status_code
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(go, range(8)))
    assert codes.count(303) == 1 and codes.count(409) == 7
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM version WHERE submission_id = 14").fetchone()[0] == 2


def test_oversized_body_is_413_and_nothing_is_written(jordan):
    before = dump()
    assert jordan.post("/resubmit/14", content=b"copy=" + b"x" * 2_000_000,
                       headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 413
    assert dump() == before


def test_security_headers_and_no_leaks_on_every_status(jordan, client):
    responses = [jordan.get("/resubmit/14"), post(jordan, data=form(copy="")), post(jordan, 9999),
                 post(jordan, headers={"Origin": "https://evil.example"}), post(jordan)]
    responses.append(post(jordan))  # 409
    for r in responses:
        assert "content-security-policy" in r.headers
        assert r.status_code == 404 or r.headers["cache-control"] == "no-store"
        assert "Traceback" not in r.text and "SELECT" not in r.text and ".py" not in r.text


def test_rejected_8_can_be_resubmitted_and_keeps_its_history(jordan):
    assert post(jordan, 8, form()).status_code == 303
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM decision WHERE submission_id = 8 AND version_number = 1"
                         ).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM version WHERE submission_id = 8").fetchone()[0] == 2


def test_banner_only_says_resubmitted_for_later_versions(mclient):
    mclient.post("/submit", data={"title": "Fresh", "product": "loan", "channel": "email",
                                  "copy": "Hello.", "launch_date": LAUNCH}, follow_redirects=False)
    with db.connect() as c:
        sid = c.execute("SELECT max(id) FROM submission").fetchone()[0]
    assert "Submitted." in mclient.get("/mine?submitted=%d" % sid).text


# ---- step 18: your changes so far ----

def check(client, sid=14, **over):
    return client.post("/resubmit/%d" % sid, data=dict(form(**over), action="check"), follow_redirects=False)


def panel(html):
    m = re.search(r'<section class="diff-panel".*?</section>', html, re.S)
    return m.group(0) if m else None


def test_one_edited_word_shows_as_removed_and_added(jordan):
    copy = stored_copy(14, 1)
    word = copy.split()[2]
    r = check(jordan, copy=copy.replace(word, "ZEBRA", 1))
    assert r.status_code == 200
    p = panel(r.text)
    assert "1 word added, 1 removed." in p
    assert re.search(r"<ins[^>]*>.*?ZEBRA</ins>", p, re.S) and re.search(r"<del[^>]*>.*?%s</del>" % re.escape(word), p, re.S)
    assert "added: " in p and "removed: " in p  # words, not color alone


def test_unchanged_message_matches_the_servers_rule(jordan):
    copy = stored_copy(14, 1)
    for variant in (copy, "\n" + copy.replace("\n", "\r\n") + "  \n"):
        assert "No changes to the copy yet" in panel(check(jordan, copy=variant).text)
        assert post(jordan, data=form(copy=variant)).status_code == 422
    assert "No changes to the copy yet" not in (panel(check(jordan, copy=copy + " x").text) or "")


def test_the_panel_appears_after_a_failed_submit_and_an_unchanged_one(jordan):
    assert panel(post(jordan, data=form(launch_date="bad")).text)
    assert panel(post(jordan, data=form(copy=stored_copy(14, 1))).text)


def test_no_panel_on_a_fresh_page(jordan):
    assert panel(jordan.get("/resubmit/14").text) is None


def test_check_writes_nothing_and_keeps_input(jordan):
    before = dump()
    r = check(jordan, copy="my draft KEEPME")
    assert r.status_code == 200 and "KEEPME" in r.text
    assert dump() == before


def test_hostile_text_is_escaped_in_the_panel(jordan):
    p = panel(check(jordan, copy=HOSTILE).text)
    assert "<script>" not in p and "<img" not in p
    assert len(tags(p, "ins")) == 1


def test_a_long_copy_still_compares_or_falls_back(jordan):
    r = check(jordan, copy=" ".join("w%d" % i for i in range(1700)))  # under 10,000 characters
    assert r.status_code == 200 and panel(r.text)
    r = check(jordan, copy="a " * 4999)
    assert r.status_code == 200 and panel(r.text)


def test_check_is_blocked_for_reviewer_other_marketer_and_cross_origin(client):
    before = dump()
    assert check(client).status_code == 403
    as_marketer(client, "Maya Chen")
    assert check(client).status_code == 403
    as_marketer(client, "Jordan Lee")
    assert client.post("/resubmit/14", data=dict(form(), action="check"),
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert dump() == before


def test_compare_copy_is_pure_and_total():
    from app import submit
    assert submit.compare_copy("a b", None) == {"state": "changed", "pieces": [("removed", "a b")], "added": 0, "removed": 2}
    assert submit.compare_copy("a b", "a b") == {"state": "unchanged"}
    assert submit.compare_copy("w " * 7000, "x") == {"state": "too_long"}


def test_the_page_explains_the_version_cap_instead_of_offering_a_form(jordan, client):
    with db.connect() as c:
        for n in range(2, 11):
            c.execute("INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
                      " VALUES (14, ?, ?, NULL, '2026-10-01T00:00:00Z')", (n, "draft %d" % n))
        c.execute("UPDATE submission SET current_version = 10 WHERE id = 14")
        c.execute("INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
                  " VALUES (14, 10, 'changes_requested', 'Alex Rivera', 'again', '2026-10-01T01:00:00Z')")
        c.commit()
    r = jordan.get("/resubmit/14")
    assert r.status_code == 200 and "limit of 10 versions" in r.text and not tags(r.text, "textarea")
    assert post(jordan, data=form(base_version="10")).status_code == 409
