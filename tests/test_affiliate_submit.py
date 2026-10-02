import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app import clock, db, submit
from app.roles import AFFILIATES

PARTNER = "Northwind Referrals"  # the default partner; owns seed #3 (in review)
OTHER = "BlueLeaf Media"  # owns seed #12 (new)
GOOD = {"title": "Partner promo", "product": "loan", "channel": "affiliate_page",
        "copy": "Guaranteed approval, 5.99%.", "notes": "First draft."}


def good(**over):
    data = dict(GOOD, launch_date=(clock.today() + timedelta(days=30)).isoformat())
    data.update(over)
    return data


def post(client, data=None, headers=None):
    return client.post("/submit", data=good() if data is None else data, headers=headers or {},
                       follow_redirects=False)


def as_partner(client, name=PARTNER):
    client.cookies.set("role", "affiliate")
    client.cookies.set("affiliate", name)
    return client


def row(sid):
    with db.connect() as c:
        return c.execute("SELECT * FROM submission WHERE id = ?", (sid,)).fetchone()


def count():
    with db.connect() as c:
        return c.execute("SELECT count(*) FROM submission").fetchone()[0]


def new_id(response):
    return int(re.search(r"submitted=(\d+)", response.headers["location"]).group(1))


def errors_in(html):
    return dict(re.findall(r'<p class="field-error" id="error-(\w+)">(.*?)</p>', html))


# ---- submit ----

def test_affiliate_submits_and_the_row_is_theirs(aclient):
    r = post(aclient)
    assert r.status_code == 303 and r.headers["location"].startswith("/mine?submitted=")
    s = row(new_id(r))
    assert (s["submitted_by"], s["channel"], s["status"], s["current_version"]) == (PARTNER, "affiliate_page", "new", 1)


@pytest.mark.parametrize("channel", ["email", "display", "paid_social", "bogus", "", "affiliate_page"])
def test_channel_is_locked_whatever_the_form_says(aclient, channel):
    s = row(new_id(post(aclient, good(channel=channel))))
    assert s["channel"] == "affiliate_page"


def test_missing_and_repeated_channel_still_lock(aclient):
    data = good()
    del data["channel"]
    assert row(new_id(post(aclient, data)))["channel"] == "affiliate_page"
    body = "title=T&product=loan&channel=email&channel=display&launch_date=%s&copy=Hello+there" % (
        clock.today() + timedelta(days=30))
    r = aclient.post("/submit", content=body, headers={"content-type": "application/x-www-form-urlencoded"},
                     follow_redirects=False)
    assert r.status_code == 303 and row(new_id(r))["channel"] == "affiliate_page"


def test_marketer_can_still_use_every_channel(mclient):
    for i, channel in enumerate(["email", "display", "paid_social", "affiliate_page"]):
        r = post(mclient, good(channel=channel, title="M %d" % i))
        assert r.status_code == 303 and row(new_id(r))["channel"] == channel


def test_affiliate_form_shows_fixed_channel_and_marketer_form_a_select(aclient):
    html = aclient.get("/submit").text
    assert 'name="channel" value="affiliate_page"' in html and 'id="field-channel"' not in html
    assert "Affiliate page" in html and "Submitting as <strong>%s</strong>" % PARTNER in html
    aclient.cookies.set("role", "marketer")
    assert 'id="field-channel"' in aclient.get("/submit").text


@pytest.mark.parametrize("field,message", [("title", "title"), ("product", "product"),
                                          ("launch_date", "date"), ("copy", "copy")])
def test_required_fields_give_inline_errors_and_keep_input(aclient, field, message):
    before = count()
    r = post(aclient, good(**{field: ""}))
    assert r.status_code == 422 and field in errors_in(r.text)
    assert count() == before
    for kept in ("title", "copy"):
        if kept != field:
            assert GOOD[kept] in r.text


@pytest.mark.parametrize("over", [{"title": "t" * (submit.MAX_TITLE_CHARS + 1)},
                                  {"copy": "c" * (submit.MAX_COPY_CHARS + 1)},
                                  {"notes": "n" * (submit.MAX_NOTES_CHARS + 1)},
                                  {"launch_date": "2026-02-30"}, {"launch_date": "soon"},
                                  {"launch_date": "2001-01-01"}, {"copy": "  \t\n "}])
def test_bad_values_are_refused(aclient, over):
    before = count()
    assert post(aclient, good(**over)).status_code == 422 and count() == before


def test_launch_warnings_still_show_for_partners(aclient):
    soon = (clock.today() + timedelta(days=1)).isoformat()
    r = post(aclient, good(launch_date=soon))
    assert r.status_code == 303
    html = aclient.get(r.headers["location"]).text
    assert "Warning:" in html


def test_reviewer_cannot_submit_and_forged_fields_are_ignored(client):
    before = count()
    r = post(client)
    assert r.status_code == 403 and "marketers and affiliate partners" in r.text and count() == before
    as_partner(client)
    r = post(client, good(submitted_by="Maya Chen", status="approved", current_version="9"))
    s = row(new_id(r))
    assert (s["submitted_by"], s["status"], s["current_version"]) == (PARTNER, "new", 1)


def test_duplicate_is_blocked_for_the_same_partner_but_not_another(aclient):
    assert post(aclient).status_code == 303
    r = post(aclient)
    assert r.status_code == 409 and "You already submitted this." in r.text and "Open it" in r.text
    as_partner(aclient, OTHER)
    assert post(aclient).status_code == 303


def test_double_click_creates_one_submission(aclient):
    before = count()
    with ThreadPoolExecutor(2) as pool:
        codes = sorted(f.result().status_code for f in [pool.submit(post, aclient) for _ in range(2)])
    assert count() == before + 1 and 303 in codes


def test_capacity_message_for_partners(aclient):
    with db.connect() as c:
        c.executemany(
            "INSERT INTO submission (title, product, channel, status, launch_date, submitted_by, created_at,"
            " current_version) VALUES (?, 'loan', 'email', 'new', '2030-01-01', 'Maya Chen', '2026-10-01T00:00:00Z', 1)",
            [("Filler %d" % i,) for i in range(submit.MAX_SUBMISSIONS - 14)])
    r = post(aclient)
    assert r.status_code == 503 and "The demo is full right now" in r.text and 'value="Partner promo"' in r.text


def test_html_in_title_and_copy_is_escaped_everywhere(aclient):
    r = post(aclient, good(title="<b>Bold</b>", copy="<script>alert(1)</script> guaranteed"))
    sid = new_id(r)
    for path in ("/mine", "/review/%d" % sid):
        html = aclient.get(path).text
        assert "<script>alert(1)</script>" not in html and "<b>Bold</b>" not in html
    aclient.cookies.set("role", "reviewer")
    assert "<b>Bold</b>" not in aclient.get("/").text


def test_cross_origin_submit_is_refused(aclient):
    before = count()
    assert post(aclient, headers={"Origin": "https://evil.example"}).status_code == 403
    assert count() == before


def test_precheck_for_partner_uses_affiliate_channel(aclient):
    # R-rules scoped to affiliate_page must fire even if the browser posted another channel.
    copy = "Guaranteed approval for everyone"
    mine = aclient.post("/submit/check", data=good(copy=copy, channel="email"), headers={"hx-request": "true"})
    aclient.cookies.set("role", "marketer")
    same = aclient.post("/submit/check", data=good(copy=copy, channel="affiliate_page"), headers={"hx-request": "true"})
    assert mine.status_code == 200 and mine.text == same.text


def test_precheck_shows_flags_and_the_not_legal_advice_note(aclient):
    html = aclient.post("/submit/check", data=good(copy="Guaranteed approval, no credit check!")).text
    assert "Guaranteed" in html and "legal advice" in html.lower()


def test_precheck_is_refused_for_reviewer(client):
    assert client.post("/submit/check", data=good()).status_code == 403


# ---- my submissions ----

def test_partner_sees_only_their_items(aclient):
    html = aclient.get("/mine").text
    assert "Mortgage prequal landing page" in html and "Long-form mortgage guide" not in html
    assert "Personal loan holiday email" not in html and "Quick cash loan landing page" not in html
    as_partner(aclient, OTHER)
    html = aclient.get("/mine").text
    assert "Long-form mortgage guide" in html and "Mortgage prequal landing page" not in html


def test_marketers_never_see_partner_items(mclient):
    for name in ("Maya Chen", "Jordan Lee"):
        mclient.cookies.set("marketer", name)
        html = mclient.get("/mine").text
        assert "Mortgage prequal landing page" not in html and "Long-form mortgage guide" not in html


def test_partner_with_nothing_sees_the_empty_state(aclient):
    as_partner(aclient, "Summit Savers")
    html = aclient.get("/mine").text
    assert "You haven't submitted anything yet." in html and 'href="/submit"' in html


def test_resubmit_is_offered_only_after_a_reply_and_only_to_the_owner(aclient):
    assert "/resubmit/3" not in aclient.get("/mine").text  # still in review: nothing to do yet
    prep(aclient, "rejected")
    assert 'href="/resubmit/3"' in aclient.get("/mine").text
    as_partner(aclient, OTHER)
    assert 'href="/resubmit/3"' not in aclient.get("/mine").text


def test_changes_requested_offers_edit_and_resubmit(aclient):
    prep(aclient, "changes_requested")
    assert "Edit and resubmit" in aclient.get("/mine").text


@pytest.mark.parametrize("query", ["submitted=3", "submitted=abc", "submitted=1&submitted=2", "submitted=99999999999999",
                                   "submitted=-1", "submitted=<script>"])
def test_submitted_banner_only_for_the_owner_and_never_echoed(aclient, query):
    as_partner(aclient, OTHER)  # seed #3 belongs to Northwind
    html = aclient.get("/mine?" + query).text
    assert "banner-submitted" not in html and "<script>" not in html


def test_submitted_banner_shows_for_the_owner(aclient):
    assert "banner-submitted" in aclient.get("/mine?submitted=3").text


def test_reviewer_views_partner_list_read_only(client):
    client.cookies.set("affiliate", OTHER)
    html = client.get("/mine").text
    assert "read-only" in html and "/resubmit/" not in html


# ---- resubmit ----
# Seed #3 is the partner's item and is still in review, so each test first has the reviewer reply.

def prep(client, outcome="rejected", sid=3, version=1):
    """A reviewer replies to the item, then the client goes back to being the item's partner."""
    client.cookies.set("role", "reviewer")
    data = {"outcome": outcome, "version": str(version), "reason": "Please fix the disclosure."}
    assert client.post("/review/%d/decision" % sid, data=data, follow_redirects=False).status_code == 303
    return as_partner(client)


def resubmit(client, sid, **over):
    data = {"copy": "A fully compliant rewrite with APR disclosed (7.9% APR).", "notes": "Fixed.",
            "launch_date": row(sid)["launch_date"], "base_version": "1"}
    data.update(over)
    return client.post("/resubmit/%d" % sid, data=data, follow_redirects=False)


@pytest.mark.parametrize("outcome", ["rejected", "changes_requested"])
def test_partner_resubmits_as_v2_with_history_kept(aclient, outcome):
    prep(aclient, outcome)
    r = resubmit(aclient, 3)
    assert r.status_code == 303 and r.headers["location"] == "/mine?submitted=3"
    s = row(3)
    assert (s["current_version"], s["status"], s["submitted_by"], s["channel"]) == (2, "in_review", PARTNER, "affiliate_page")
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM decision WHERE submission_id = 3").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM version WHERE submission_id = 3").fetchone()[0] == 2
    aclient.cookies.set("role", "reviewer")
    html = aclient.get("/review/3").text
    assert "Northwind Referrals" in html and "v2" in html and "partner-badge" in html


def test_resubmit_form_is_offered_and_check_flags_writes_nothing(aclient):
    prep(aclient)
    assert "<textarea" in aclient.get("/resubmit/3").text
    r = aclient.post("/resubmit/3", data={"copy": "Totally different words here.", "notes": "",
                                          "launch_date": row(3)["launch_date"], "base_version": "1",
                                          "action": "check"})
    assert r.status_code == 200 and row(3)["current_version"] == 1


def test_resubmit_unchanged_copy_is_blocked(aclient):
    prep(aclient)
    with db.connect() as c:
        same = c.execute("SELECT copy FROM version WHERE submission_id = 3").fetchone()["copy"]
    r = resubmit(aclient, 3, copy=same)
    assert r.status_code == 422 and "Nothing has changed" in r.text and row(3)["current_version"] == 1


@pytest.mark.parametrize("over", [{"copy": ""}, {"copy": "x" * (submit.MAX_COPY_CHARS + 1)},
                                  {"launch_date": "nope"}])
def test_resubmit_validation_keeps_input_and_writes_nothing(aclient, over):
    prep(aclient)
    r = resubmit(aclient, 3, **over)
    assert r.status_code == 422 and row(3)["current_version"] == 1


@pytest.mark.parametrize("base", ["2", "0", "abc", ""])
def test_stale_or_garbage_base_version_saves_nothing(aclient, base):
    prep(aclient)
    r = resubmit(aclient, 3, base_version=base)
    assert r.status_code == 409 and row(3)["current_version"] == 1


def test_resubmit_twice_is_stale_the_second_time(aclient):
    prep(aclient)
    assert resubmit(aclient, 3).status_code == 303
    r = resubmit(aclient, 3, copy="Yet another distinct rewrite of the copy.")
    assert r.status_code == 409 and row(3)["current_version"] == 2


@pytest.mark.parametrize("sid", [3, 12, 7, 1])  # in review, new, approved, new: none can be resubmitted
def test_locked_or_waiting_items_have_no_form(aclient, sid):
    if sid == 12:
        as_partner(aclient, OTHER)
    html = aclient.get("/resubmit/%d" % sid).text
    assert "<textarea" not in html
    assert resubmit(aclient, sid).status_code in (403, 409)


def test_ownership_is_enforced_both_ways(aclient):
    prep(aclient)
    as_partner(aclient, OTHER)
    html = aclient.get("/resubmit/3").text
    assert "belongs to Northwind Referrals" in html and "<textarea" not in html
    r = resubmit(aclient, 3)
    assert r.status_code == 403 and row(3)["current_version"] == 1
    aclient.cookies.set("role", "marketer")
    aclient.cookies.set("marketer", "Maya Chen")
    assert resubmit(aclient, 3).status_code == 403
    # a partner cannot take over a marketer's changes-requested item (#14) or rejected item (#8)
    as_partner(aclient)
    for sid in (14, 8):
        r = resubmit(aclient, sid)
        assert r.status_code == 403 and row(sid)["current_version"] == 1


def test_a_marketer_still_resubmits_their_own_rejected_item(client):
    client.cookies.set("role", "marketer")
    client.cookies.set("marketer", "Jordan Lee")
    assert resubmit(client, 8).status_code == 303 and row(8)["current_version"] == 2


def test_reviewer_cannot_resubmit_and_bad_ids_404(client):
    assert resubmit(client, 3).status_code == 403
    as_partner(client)
    assert client.get("/resubmit/abc").status_code == 404
    assert client.get("/resubmit/9999").status_code == 404
    assert client.post("/resubmit/9999", data={}).status_code == 404


def test_cross_origin_resubmit_is_refused(aclient):
    prep(aclient)
    r = aclient.post("/resubmit/3", data={"copy": "Other words entirely here.", "base_version": "1",
                                          "launch_date": row(3)["launch_date"]},
                     headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and row(3)["current_version"] == 1


def test_fixed_fields_cannot_change_on_resubmit(aclient):
    prep(aclient)
    before = row(3)
    assert resubmit(aclient, 3, title="Hijack", product="card", channel="email", submitted_by="Maya Chen").status_code == 303
    after = row(3)
    assert (after["title"], after["product"], after["channel"], after["submitted_by"]) == (
        before["title"], before["product"], before["channel"], before["submitted_by"])


def test_version_cap_blocks_the_eleventh_version(aclient):
    prep(aclient)
    for n in range(1, submit.MAX_VERSIONS):
        assert resubmit(aclient, 3, copy="Rewrite number %d with different words." % n, base_version=str(n)).status_code == 303
        prep(aclient, "rejected", version=n + 1)
    assert row(3)["current_version"] == submit.MAX_VERSIONS
    r = resubmit(aclient, 3, copy="One rewrite too many.", base_version=str(submit.MAX_VERSIONS))
    assert r.status_code == 409 and row(3)["current_version"] == submit.MAX_VERSIONS


# ---- reviewer side on partner items ----

def reviewer(client):
    client.cookies.set("role", "reviewer")
    return client


def test_partner_cannot_decide_dismiss_or_comment(aclient):
    for path, data in (("/review/12/decision", {"outcome": "approved", "version": "1"}),
                       ("/review/12/dismiss", {"rule_id": "R1", "version": "1", "note": "x"}),
                       ("/review/12/comment", {"text": "hi", "version": "1"})):
        assert aclient.post(path, data=data, follow_redirects=False).status_code == 403
    assert row(12)["status"] == "new"


def test_reviewer_can_decide_a_partner_item_once_and_it_locks(aclient):
    reviewer(aclient)
    assert aclient.post("/review/12/decision", data={"outcome": "approved", "version": "1"},
                        follow_redirects=False).status_code == 303
    assert row(12)["status"] == "approved"
    again = aclient.post("/review/12/decision", data={"outcome": "rejected", "version": "1", "reason": "Because"},
                         follow_redirects=False)
    assert again.status_code != 303 and row(12)["status"] == "approved"


def test_reason_is_required_to_reject_a_partner_item(aclient):
    reviewer(aclient)
    r = aclient.post("/review/12/decision", data={"outcome": "rejected", "version": "1"}, follow_redirects=False)
    assert r.status_code != 303 and row(12)["status"] == "new"


def test_review_page_and_trail_name_the_partner(aclient):
    reviewer(aclient)
    html = aclient.get("/review/3").text
    assert "Submitted by Northwind Referrals" in html and "partner-badge" in html
    assert aclient.get("/review/1").text.count("partner-badge") == 0


def test_every_affiliate_is_a_known_name():
    assert len(set(AFFILIATES)) == len(AFFILIATES) == 3
