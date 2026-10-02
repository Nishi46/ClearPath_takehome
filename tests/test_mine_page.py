import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import db
from tests.helpers import tamper


def headings(html, tag):
    return [re.sub(r"<[^>]+>|\s+", " ", h).strip() for h in re.findall(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), html, re.S)]


def items(html):
    return re.findall(r'<li class="mine-item[^"]*">(.*?)</li>', html, re.S)


def item(html, title_part):
    return next(i for i in items(html) if title_part in i)


def as_marketer(client, name):
    client.cookies.set("role", "marketer")
    client.post("/marketer", data={"name": name})
    return client.get("/mine")


def text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def test_mayas_page_has_group_headings_with_counts(mclient):
    html = mclient.get("/mine").text
    assert headings(html, "h1") == ["My submissions"]
    assert headings(html, "h2") == ["In progress (5)", "Done (2)"]      # nothing needs her action: no empty group
    assert "Needs your action" not in html
    assert len(items(html)) == 7


def test_jordans_item_14_needs_action_with_the_seed_reason_and_an_edit_action(mclient):
    html = as_marketer(mclient, "Jordan Lee").text
    assert headings(html, "h2") == ["Needs your action (2)", "In progress (2)", "Done (1)"]
    first = items(html)[0]
    with db.connect() as c:
        stored = c.execute("SELECT reason FROM decision WHERE submission_id = 14").fetchone()[0]
    assert 'href="/review/14"' in first and "Changes requested" in first and "Alex Rivera" in first
    assert stored.split(".")[0] in text(first)
    assert 'href="/resubmit/14"' in first and "Edit and resubmit" in first


def test_rejected_item_8_offers_fix_and_resubmit_as_new_version(mclient):
    html = as_marketer(mclient, "Jordan Lee").text
    row = items(html)[1]
    assert 'href="/review/8"' in row and "Rejected" in row and 'href="/resubmit/8"' in row
    assert "Fix and resubmit as new version" in row and "Edit and resubmit" not in row


def test_approved_item_only_gets_view(mclient):
    row = item(mclient.get("/mine").text, "/review/6\"")
    assert "Approved" in row and "View" in row and "/resubmit/" not in row
    assert "Alex Rivera" in row                          # the approval is shown as feedback


def test_in_progress_items_show_view_not_resubmit(mclient):
    html = mclient.get("/mine").text
    for row in items(html):
        if "status-new" in row or "status-in_review" in row:
            assert "/resubmit/" not in row and ">View<" in row


def test_item_without_a_decision_has_no_feedback_block(mclient):
    html = mclient.get("/mine").text
    assert "mine-feedback" not in item(html, 'href="/review/9"')


def test_an_old_decision_is_not_shown_as_current_feedback(mclient):
    assert "mine-feedback" not in item(mclient.get("/mine").text, 'href="/review/5"')   # v2 is waiting; v1 was decided


def test_status_urgency_and_flags_are_written_in_words(mclient):
    row = item(mclient.get("/mine").text, 'href="/review/1"')
    assert re.search(r'class="status status-new">New<', row)
    assert "Rush: launches tomorrow" in row and "open flag" in row and "v1" in row
    assert "Loan / Email" in row


def test_a_marketer_with_nothing_gets_an_empty_state_with_a_next_action(mclient):
    html = as_marketer(mclient, "Sam Patel").text
    assert "You haven't submitted anything yet." in html
    assert 'role="status"' in html and 'href="/submit"' in html
    assert headings(html, "h2") == [] and items(html) == []


def test_the_page_basics(mclient):
    r = mclient.get("/mine")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.text.count("<h1") == 1 and "<title>My submissions" in r.text
    assert 'href="/submit"' in r.text and "Viewing as:" in r.text


def test_hostile_titles_and_reasons_are_escaped(mclient):
    evil = "<script>alert(1)</script><img src=x onerror=alert(1)>"
    with db.connect() as c:
        c.execute("UPDATE submission SET title = ? WHERE id = 6", (evil,))
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 6", (evil,))
    html = mclient.get("/mine").text
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_a_long_unbroken_reason_is_wrapped_by_the_stylesheet(mclient):
    with db.connect() as c:
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 6", ("x" * 2000,))
    assert "x" * 2000 in mclient.get("/mine").text
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "style.css").read_text()
    assert re.search(r"\.mine-item\s*{[^}]*overflow-wrap:\s*anywhere", css)


def test_every_row_has_a_real_link_to_its_item(mclient):
    for row in items(mclient.get("/mine").text):
        assert re.search(r'<h3 class="mine-title"><a href="/review/\d+">', row)
        assert re.search(r'<a class="button-link" href="/(review|resubmit)/\d+">', row)


def test_action_links_name_their_item_for_screen_readers(mclient):
    row = item(as_marketer(mclient, "Jordan Lee").text, 'href="/review/14"')
    assert 'class="visually-hidden"' in row and text(row).count("Home equity") >= 2


def test_reviewer_sees_it_read_only_with_no_resubmit_actions_at_all(client):
    client.cookies.set("role", "reviewer")
    client.post("/marketer", data={"name": "Jordan Lee"})
    html = client.get("/mine").text
    assert "/resubmit/" not in html and "Edit and resubmit" not in html and 'href="/submit"' not in html.split("<main")[1]
    assert "You are viewing Jordan Lee" in html and "read-only" in html
    assert len(items(html)) == 5 and "Needs your action (2)" in text(html)


def test_reviewer_looking_at_an_empty_marketer(client):
    client.post("/marketer", data={"name": "Sam Patel"})
    html = client.get("/mine").text
    assert "Sam Patel hasn't submitted anything yet. Choose another marketer above." in html
    assert "Submit your first" not in html


def test_the_page_is_not_affected_by_query_values(mclient):
    html = mclient.get("/mine?name=Jordan+Lee&role=reviewer&x=<script>").text
    assert len(items(html)) == 7 and "<script>" not in html


def test_post_is_not_allowed(mclient):
    assert mclient.post("/mine").status_code == 405


def test_the_page_writes_nothing(mclient):
    with db.connect() as c:
        before = [c.execute("SELECT count(*) FROM %s" % t).fetchone()[0] for t in ("submission", "version", "decision")]
    mclient.get("/mine")
    with db.connect() as c:
        assert [c.execute("SELECT count(*) FROM %s" % t).fetchone()[0] for t in ("submission", "version", "decision")] == before


def test_security_headers(mclient):
    assert "default-src 'self'" in mclient.get("/mine").headers["content-security-policy"]


def test_malformed_stored_dates_do_not_break_the_page(mclient):
    with db.connect() as c:
        c.execute("UPDATE submission SET launch_date = 'garbage' WHERE id = 3")
        tamper(c, "UPDATE decision SET created_at = 'garbage' WHERE submission_id = 6")
    r = mclient.get("/mine")
    assert r.status_code == 200 and "unknown date" in r.text


def test_a_new_submission_appears_in_progress(mclient):
    from tests.test_submit_route import good, post
    post(mclient, good(title="Brand new thing"))
    html = mclient.get("/mine").text
    assert "In progress (6)" in text(html) and "Brand new thing" in html


def test_role_switch_lands_on_mine_for_a_marketer(client):
    r = client.post("/role", data={"role": "marketer"}, follow_redirects=True)
    assert r.status_code == 200 and "<h1>My submissions</h1>" in r.text


# ---- narrow screens (real browser) ----

@pytest.mark.skipif(not Path("/Applications/Google Chrome.app").exists(), reason="Google Chrome is not installed here")
@pytest.mark.parametrize("size", [(1366, 768), (390, 844)])
def test_no_horizontal_scroll_with_long_unbroken_text(mclient, size):
    from tests.chrome_layout import measure
    with db.connect() as c:
        tamper(c, "UPDATE decision SET reason = ? WHERE submission_id = 6", ("Unbroken" * 625,))
        c.execute("UPDATE submission SET title = ? WHERE id = 3", ("T" * 120,))
    m = measure(mclient.get("/mine").text, *size)
    assert m["scrollWidth"] <= m["clientWidth"] + 1 and m["wide"] == [], m["wide"]
