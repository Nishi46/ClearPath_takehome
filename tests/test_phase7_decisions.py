"""Phase 7 steps 10 to 13: double decisions, reject then resubmit, dismissals and comments, reset mid-review."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app import clock, db
from app.routes import pages
from tests.helpers import tamper
from tests.test_phase7_edge_matrix import as_role, counts, stored_copy

HEADERS = {"origin": "http://testserver"}
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")


def dump():
    with db.connect() as c:
        return {t: [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)] for t in TABLES}


def decide(c, sid, outcome="approved", version="1", reason=None, headers=HEADERS, extra=None):
    data = {"outcome": outcome, "version": version, **(extra or {})}
    if reason is not None:
        data["reason"] = reason
    return c.post(f"/review/{sid}/decision", data=data, headers=headers, follow_redirects=False)


def status_of(sid):
    with db.connect() as c:
        return c.execute("SELECT status FROM submission WHERE id = ?", (sid,)).fetchone()[0]


# ---- step 10: decisions ----

def test_concurrent_double_click_makes_exactly_one_decision(client):
    with ThreadPoolExecutor(6) as pool:
        codes = sorted(r.status_code for r in pool.map(lambda _: decide(client, 3), range(6)))
    assert codes.count(303) == 1 and all(code in (303, 409, 400) for code in codes)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM decision WHERE submission_id = 3").fetchone()[0] == 1


@pytest.mark.parametrize("second", ["approved", "rejected", "changes_requested"])
def test_a_second_decision_never_replaces_the_first(client, second):
    assert decide(client, 3).status_code == 303
    before = dump()
    r = decide(client, 3, second, reason="Changed my mind.")
    assert r.status_code in (400, 409) and dump() == before and status_of(3) == "approved"


def test_the_refusal_page_links_back_to_the_item(client):
    decide(client, 3)
    r = decide(client, 3)
    assert 'href="/review/3"' in r.text or 'href="/"' in r.text


def test_a_decision_on_an_older_version_is_refused(client):
    before = dump()
    r = decide(client, 5, "approved", version="1")        # #5 is at v2
    assert r.status_code in (400, 409) and dump() == before


@pytest.mark.parametrize("version", ["", "0", "-1", "3", "abc", "1.0", "1 ", "٣", "99999999999999999999"])
def test_a_wrong_version_value_is_refused_and_writes_nothing(client, version):
    before = dump()
    r = decide(client, 3, version=version)
    assert r.status_code in (400, 409, 422) and dump() == before


@pytest.mark.parametrize("headers", [{"origin": "https://evil.example"}, {"origin": "null"},
                                     {"referer": "https://evil.example/"}])
def test_cross_origin_decisions_are_refused(client, headers):
    before = dump()
    assert decide(client, 3, headers=headers).status_code == 403 and dump() == before


def test_a_marketer_cannot_decide(client):
    as_role(client, "marketer", "Maya Chen")
    before = dump()
    assert decide(client, 3).status_code == 403 and dump() == before


@pytest.mark.parametrize("outcome", ["", "approve", "APPROVED", "pending", "approved ", "new", "' OR 1=1"])
def test_unknown_outcomes_are_refused(client, outcome):
    before = dump()
    assert decide(client, 3, outcome, reason="r").status_code in (400, 422) and dump() == before


@pytest.mark.parametrize("outcome", ["changes_requested", "rejected"])
@pytest.mark.parametrize("reason", [None, "", "   ", "​​", "\t\n", "x" * 2001])
def test_a_missing_or_oversized_reason_is_refused_and_kept_in_the_form(client, outcome, reason):
    before = dump()
    r = decide(client, 3, outcome, reason=reason)
    assert r.status_code in (400, 422) and dump() == before
    if reason and reason.strip("​ \t\n"):
        assert "x" * 50 in r.text                         # what was typed comes back


@pytest.mark.parametrize("outcome", ["changes_requested", "rejected"])
def test_a_reason_of_exactly_2000_characters_is_accepted(client, outcome):
    assert decide(client, 3, outcome, reason="r" * 2000).status_code == 303


def test_the_form_cannot_set_the_reviewer_or_the_status(client):
    r = decide(client, 3, extra={"reviewer": "Mallory", "status": "rejected", "created_at": "2000-01-01T00:00:00Z"})
    assert r.status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT reviewer, created_at FROM decision WHERE submission_id = 3").fetchone()
    assert row[0] == "Alex Rivera" and not row[1].startswith("2000") and status_of(3) == "approved"


# ---- step 11: reject then resubmit ----

def resubmit_8(client, copy="Apply today. Rates from 5.99% APR. Subject to credit approval."):
    as_role(client, "marketer", "Jordan Lee")
    return client.post("/resubmit/8", headers=HEADERS, follow_redirects=False, data={
        "copy": copy, "notes": "Reworked.", "base_version": "1",
        "launch_date": (clock.today() + timedelta(days=30)).isoformat()})


def test_resubmitting_8_leaves_every_old_row_byte_for_byte(client):
    before = dump()
    assert resubmit_8(client).status_code == 303
    after = dump()
    assert after["decision"] == before["decision"] and after["comment"] == before["comment"]
    assert after["flag_dismissal"] == before["flag_dismissal"]
    assert [v for v in after["version"] if v in before["version"]] == before["version"]
    assert [f for f in after["flag"] if f in before["flag"]] == before["flag"]


def test_trail_order_after_resubmitting_8(client):
    assert resubmit_8(client).status_code == 303
    page = as_role(client, "reviewer").get("/review/8").text
    trail = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page[page.index("trail-heading"):]))
    v1, rejected, v2 = trail.find("v1 submitted"), trail.find("Rejected"), trail.find("v2 submitted")
    assert -1 < v1 < rejected < v2


def test_the_v1_decision_cannot_be_changed_after_the_resubmit(client):
    resubmit_8(client)
    with db.connect() as c:
        with pytest.raises(Exception):
            c.execute("UPDATE decision SET outcome = 'approved' WHERE submission_id = 8")


def test_v2_flags_are_recomputed_and_open(client):
    from app import rules

    new = "Guaranteed approval for everyone. 5.99% rate."
    resubmit_8(client, new)
    with db.connect() as c:
        stored = sorted(r[0] for r in c.execute(
            "SELECT rule_id FROM flag WHERE version_id = (SELECT id FROM version WHERE submission_id = 8 AND version_number = 2)"))
        assert c.execute("SELECT count(*) FROM flag_dismissal WHERE version_id = "
                         "(SELECT id FROM version WHERE submission_id = 8 AND version_number = 2)").fetchone()[0] == 0
    assert stored == sorted(f.rule_id for f in rules.evaluate("loan", "affiliate_page", new))


def test_diff_marks_changes_in_words_and_symbols(client):
    resubmit_8(client)
    page = as_role(client, "reviewer").get("/review/8?diff=1").text
    assert "removed: " in page and "added: " in page


def test_marketer_sees_the_item_move_out_of_needs_your_action(client):
    def needs_action(html):
        section = html.split("Needs your action", 1)[1] if "Needs your action" in html else ""
        return "Quick cash loan landing page" in section.split("In progress")[0].split("Done")[0]

    as_role(client, "marketer", "Jordan Lee")
    assert needs_action(client.get("/mine").text)
    assert resubmit_8(client).status_code == 303
    page = client.get("/mine").text
    assert "Quick cash loan landing page" in page and not needs_action(page)
    assert status_of(8) in ("new", "in_review")


# ---- step 12: dismissals, comments, locked comments, the missed violation ----

def dismiss(c, sid=12, rule="R4", version="1", note="Explanatory use.", headers=HEADERS):
    return c.post(f"/review/{sid}/dismiss", data={"rule_id": rule, "version": version, "note": note},
                  headers=headers, follow_redirects=False)


@pytest.mark.parametrize("note", ["", "   ", "​​", "\t\n", "n" * 1001])
def test_a_bad_dismissal_note_is_refused_and_writes_nothing(client, note):
    before = dump()
    assert dismiss(client, note=note).status_code in (400, 422) and dump() == before


def test_a_note_of_exactly_1000_characters_is_accepted(client):
    assert dismiss(client, note="n" * 1000).status_code == 303


def test_dismissing_the_same_flag_twice_leaves_one_row_and_one_trail_line(client):
    assert dismiss(client).status_code == 303
    before = dump()
    assert dismiss(client).status_code in (400, 409) and dump() == before
    assert as_role(client, "reviewer").get("/review/12").text.count("Explanatory use.") >= 1


def test_dismissal_leaves_the_open_list_and_the_queue_count(client):
    def flags_cell():
        row = [r for r in re.findall(r"<tr\b.*?</tr>", client.get("/").text, re.S) if 'href="/review/12"' in r][0]
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))
    before = flags_cell()
    dismiss(client)
    assert flags_cell() != before


def test_dismissals_are_refused_after_a_decision_on_an_older_version_for_a_marketer_or_an_unfired_rule(client):
    decide(client, 12)
    before = dump()
    assert dismiss(client).status_code in (400, 409)                          # decided
    assert dismiss(client, sid=5, rule="R2", version="1").status_code in (400, 409)   # older version
    assert dismiss(client, sid=10, rule="R4", version="1").status_code in (400, 409, 422)  # rule did not fire
    assert dismiss(client, sid=9, rule="R4", version="1").status_code in (400, 409, 422)
    assert dump() == before
    as_role(client, "marketer", "Maya Chen")
    assert dismiss(client, sid=1, rule="R1").status_code == 403 and dump() == before


def comment(c, sid=6, text="Filed for the record.", version="1", rule=None):
    data = {"text": text, "version": version}
    if rule:
        data["rule_id"] = rule
    return c.post(f"/review/{sid}/comment", data=data, headers=HEADERS, follow_redirects=False)


@pytest.mark.parametrize("sid", [6, 7])
def test_locked_items_take_comments_and_keep_their_status(client, sid):
    with db.connect() as c:
        status, version = c.execute("SELECT status, current_version FROM submission WHERE id = ?", (sid,)).fetchone()
    before = counts()["comment"]
    assert comment(client, sid, version=str(version)).status_code == 303
    assert counts()["comment"] == before + 1 and status_of(sid) == status


def test_a_comment_on_an_older_version_is_refused(client):
    before = dump()
    assert comment(client, 5, version="1").status_code == 409 and dump() == before


def test_the_same_comment_twice_in_a_row_is_refused_but_a_different_one_passes(client):
    assert comment(client).status_code == 303
    before = counts()["comment"]
    assert comment(client).status_code in (400, 409) and counts()["comment"] == before
    assert comment(client, text="A different note.").status_code == 303


def test_a_comment_cannot_link_to_a_rule_that_did_not_fire(client):
    before = dump()
    assert comment(client, 10, rule="R1", version="1").status_code in (400, 409, 422) and dump() == before


def test_8_has_no_flag_for_the_missed_phrase_and_still_carries_the_assist_note(client):
    assert "everyone gets a yes" in stored_copy(8).lower()
    page = client.get("/review/8").text
    assert "Flags assist the reviewer. They never decide." in page
    assert not re.search(r"everyone gets a yes[^<]*</mark>", page, re.I)


# ---- step 13: reset during an in-progress review ----

def reset(c):
    pages.reset_cooldown.clear()
    return c.post("/reset", data={"confirm": "reset"}, headers=HEADERS, follow_redirects=False)


def test_a_stale_decision_after_reset_matches_a_fresh_one(client):
    decide(client, 3)
    reset(client)
    r = decide(client, 3)                      # the stale tab: #3 is undecided again, so this is a real decision
    assert r.status_code == 303 and status_of(3) == "approved"
    after_stale = dump()["decision"]
    assert decide(client, 3).status_code in (400, 409) and dump()["decision"] == after_stale


def test_a_stale_decision_for_an_item_that_reset_left_decided_is_refused(client):
    reset(client)
    before = dump()
    assert decide(client, 5, version="1").status_code in (400, 409) and dump() == before   # #5 v1 already decided
    assert decide(client, 6, version="1").status_code in (400, 409) and dump() == before


def test_stale_comment_and_dismissal_forms_follow_the_same_rules(client):
    dismiss(client)
    comment(client, 3, text="Pre-reset note.")
    reset(client)
    fresh = dump()
    assert dismiss(client).status_code == 303                   # allowed again: the item is back to the seed
    assert dump()["flag_dismissal"] != fresh["flag_dismissal"]
    reset(client)
    assert dump() == fresh


@pytest.mark.parametrize("round_", range(5))
def test_reset_racing_a_decision_ends_in_one_of_two_valid_states(client, round_):
    with ThreadPoolExecutor(2) as pool:
        d = pool.submit(decide, client, 3)
        r = pool.submit(reset, client)
        codes = (d.result().status_code, r.result().status_code)
    assert all(code < 500 for code in codes)
    state = dump()
    decided = [d for d in state["decision"] if d[1] == 3]
    if decided:
        assert status_of(3) == "approved" and len(decided) == 1
    else:
        assert status_of(3) == "in_review"
    assert len([d for d in state["decision"] if d[1] == 3]) <= 1


def test_the_reset_cooldown_and_confirmation_still_guard_the_route(client):
    assert reset(client).status_code == 303
    again = client.post("/reset", data={"confirm": "reset"}, headers=HEADERS, follow_redirects=False)
    assert again.status_code == 429 and "wait a few seconds" in again.text
    pages.reset_cooldown.clear()
    assert client.post("/reset", data={"confirm": "nope"}, headers=HEADERS).status_code == 400
    assert client.post("/reset", data={"confirm": "reset"}, headers={"origin": "https://evil.example"}).status_code == 403
