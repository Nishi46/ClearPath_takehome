"""Phase 7, part A: one test class per row of seed-data.md section 3 (the edge-case matrix).

Each class drives the real app on a freshly seeded database. They record what the app does today;
rows that do not yet behave as phase-7-steps.md says are marked xfail with the step that fixes them,
so a gap is visible and fixing it turns the xfail into a failure to remove the marker.

| Row (seed-data.md section 3)                       | Test class                  | Hardened in step |
|----------------------------------------------------|-----------------------------|------------------|
| No flags found                                     | TestNoFlagsFound            | 3                |
| Empty filter results                               | TestEmptyFilterResults      | 4                |
| Empty queue                                        | TestEmptyQueue              | 5                |
| Missing fields, whitespace-only copy               | TestMissingFieldsAndBlankCopy | 6              |
| Very long copy                                     | TestVeryLongCopy            | 7                |
| Launch in the past                                 | TestLaunchInThePast         | 8                |
| Launch within 2 business days                      | TestLaunchWithinTwoBusinessDays | 8            |
| Resubmit without changes                           | TestResubmitWithoutChanges  | 9                |
| Double-click a decision; decide on an already-decided version | TestDecisionTwice | 10             |
| Reject then resubmit, history preserved            | TestRejectThenResubmit      | 11               |
| Flag false positive: dismissed / live              | TestFalsePositiveFlags      | 12               |
| Comment on a locked version                        | TestCommentOnLockedVersion  | 12               |
| Reset during an in-progress review                 | TestResetDuringReview       | 13               |
| Violation the rules miss                           | TestViolationTheRulesMiss   | 12               |
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import clock, db
from tests.conftest import pin_clock
from tests.test_queue_page import body_rows, row_for

DOC = Path(__file__).resolve().parent.parent / "documentation" / "seed-data.md"

# Edge-case name in the doc (first table cell) -> the class that covers it.
# Filled in at the bottom of the module, once the classes exist.
MATRIX = {}

NO_FLAGS = "No flags detected."
RULES_NOT_ADVICE = "Rules are illustrative, not legal advice."
FLAGS_ASSIST = "Flags assist the reviewer. They never decide."


def good_form(**over):
    data = {"title": "Spring promo", "product": "loan", "channel": "email", "notes": "",
            "copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
            "launch_date": (clock.today() + timedelta(days=30)).isoformat()}
    data.update(over)
    return data


def as_role(client, role="reviewer", marketer=None):
    client.cookies.set("role", role)
    if marketer:
        client.cookies.set("marketer", marketer)
    return client


def counts():
    with db.connect() as c:
        return {t: c.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
                for t in ("submission", "version", "flag", "flag_dismissal", "decision", "comment")}


def stored_copy(sid, number=1):
    with db.connect() as c:
        return c.execute("SELECT copy FROM version WHERE submission_id = ? AND version_number = ?",
                         (sid, number)).fetchone()[0]


def alerts(html):
    return " ".join(re.findall(r'role="alert"[^>]*>(.*?)</(?:p|div)>', html, re.S))


def row_text(client, sid, query=""):
    return re.sub(r"<[^>]+>", " ", re.sub(r"\s+", " ", row_for(client.get("/" + query).text, sid)))


class TestNoFlagsFound:
    """#6 and #10 are clean: the review page says so and still carries the assist-only note."""

    @pytest.mark.parametrize("sid", [6, 10])
    def test_review_page_says_no_flags_detected(self, client, sid):
        html = client.get(f"/review/{sid}").text
        assert NO_FLAGS in html and "No open flags" not in html
        assert FLAGS_ASSIST in html and RULES_NOT_ADVICE in html

    @pytest.mark.parametrize("sid", [6, 10])
    def test_queue_shows_zero_flags_in_words(self, client, sid):
        assert "No flags" in row_text(client, sid)

    def test_near_miss_guarantee_wording_on_10_does_not_fire_r1(self, client):
        assert "money-back guarantee" in stored_copy(10).lower()
        with db.connect() as c:
            assert c.execute("SELECT count(*) FROM flag WHERE version_id IN "
                             "(SELECT id FROM version WHERE submission_id = 10)").fetchone()[0] == 0


class TestEmptyFilterResults:
    def test_rejected_card_has_no_results_and_a_way_out(self, client):
        html = client.get("/?status=rejected&product=card").text
        assert "No items match these filters." in html and 'href="/">Clear filters' in html
        assert "<tbody>" not in html or not body_rows(html)

    def test_clearing_filters_brings_back_all_14(self, client):
        assert len(body_rows(client.get("/").text)) == 14

    def test_unknown_filter_values_do_not_crash(self, client):
        r = client.get("/?status=bogus&product=%27+OR+1%3D1+--&channel=%3Cscript%3E")
        assert r.status_code == 200 and "<script>" not in r.text


class TestEmptyQueue:
    """Not seedable: the fixture empties the table after startup, as a scratch database would be."""

    @pytest.fixture
    def empty(self, client):
        with db.connect() as c:
            c.execute("DELETE FROM submission")
        assert counts()["submission"] == 0
        return client

    @pytest.mark.parametrize("role", ["reviewer", "marketer"])
    def test_empty_queue_has_copy_and_a_next_action(self, empty, role):
        r = as_role(empty, role).get("/")
        assert r.status_code == 200
        assert "No submissions yet." in r.text and 'href="/submit"' in r.text
        assert not body_rows(r.text) if "<tbody>" in r.text else True

    def test_reading_the_empty_queue_does_not_seed_it(self, empty):
        empty.get("/")
        assert counts()["submission"] == 0


class TestMissingFieldsAndBlankCopy:
    @pytest.fixture
    def maya(self, client):
        return as_role(client, "marketer", "Maya Chen")

    @pytest.mark.parametrize("field", ["title", "product", "channel", "launch_date", "copy"])
    def test_a_missing_field_is_refused_and_writes_nothing(self, maya, field):
        before = counts()
        r = maya.post("/submit", data=good_form(**{field: ""}), follow_redirects=False)
        assert r.status_code in (400, 422)
        assert 'class="field-error"' in r.text and f'id="error-{field}"' in r.text
        assert counts() == before

    @pytest.mark.parametrize("blank", ["   ", "\t\n  \n", "​​", "   "])
    def test_blank_copy_is_refused_and_input_is_kept(self, maya, blank):
        before = counts()
        r = maya.post("/submit", data=good_form(copy=blank, title="Keep me"), follow_redirects=False)
        assert r.status_code in (400, 422) and 'id="error-copy"' in r.text
        assert 'value="Keep me"' in r.text and counts() == before


class TestVeryLongCopy:
    """#12 is the long item (500+ words)."""

    def test_12_has_over_500_words(self, client):
        assert len(stored_copy(12).split()) >= 500

    def test_12_renders_with_its_flag_and_decision_form(self, client):
        r = client.get("/review/12")
        assert r.status_code == 200 and "pre-approved" in r.text
        assert 'action="/review/12/decision"' in r.text

    def test_12_queue_row_is_still_one_row_linking_to_it(self, client):
        assert row_for(client.get("/").text, 12).count('href="/review/12"') == 1


class TestLaunchInThePast:
    @pytest.mark.weekday("wednesday")
    def test_11_is_overdue_in_words_and_first_in_the_queue(self, client):
        html = client.get("/").text
        assert "Overdue" in row_for(html, 11) and "urgency-overdue" in row_for(html, 11)
        assert 'href="/review/11"' in body_rows(html)[0]

    def test_a_past_launch_date_warns_on_the_form_and_still_submits(self, client):
        as_role(client, "marketer", "Maya Chen")
        past = (clock.today() - timedelta(days=3)).isoformat()
        r = client.post("/submit", data=good_form(launch_date=past), follow_redirects=False)
        assert r.status_code == 303, "a past launch date warns but does not block (decision 1)"


class TestLaunchWithinTwoBusinessDays:
    @pytest.mark.parametrize("day", ["monday", "wednesday", "friday"])
    def test_1_and_2_are_rush_on_any_weekday(self, day, monkeypatch, db_path):
        from app.main import app

        pin_clock(monkeypatch, day)
        with TestClient(app) as c:
            for sid in (1, 2):
                assert "launches" in row_text(c, sid).lower() and "urgency-rush" in row_for(c.get("/").text, sid)

    @pytest.mark.parametrize("day,rush", [("monday", False), ("wednesday", True), ("friday", True)])
    def test_14_depends_on_the_weekday(self, day, rush, monkeypatch, db_path):
        from app.main import app

        pin_clock(monkeypatch, day)
        with TestClient(app) as c:
            assert ("urgency-rush" in row_for(c.get("/").text, 14)) is rush

    @pytest.mark.weekday("wednesday")
    def test_a_rush_date_warns_on_the_form_and_still_submits(self, client):
        as_role(client, "marketer", "Maya Chen")
        soon = (clock.today() + timedelta(days=1)).isoformat()
        r = client.post("/submit", data=good_form(launch_date=soon), follow_redirects=False)
        assert r.status_code == 303


class TestResubmitWithoutChanges:
    """#14 (changes requested, owned by Jordan Lee): unchanged copy is blocked, not warned."""

    @pytest.fixture
    def jordan(self, client):
        return as_role(client, "marketer", "Jordan Lee")

    def resubmit(self, client, copy):
        return client.post("/resubmit/14", follow_redirects=False,
                           data={"copy": copy, "notes": "", "base_version": "1",
                                 "launch_date": (clock.today() + timedelta(days=30)).isoformat()})

    def test_identical_copy_is_blocked_and_makes_no_version(self, jordan):
        before = counts()
        r = self.resubmit(jordan, stored_copy(14))
        assert r.status_code in (400, 409, 422) and "same as" in r.text.lower()
        assert counts() == before

    def test_line_ending_and_trailing_space_changes_are_still_unchanged(self, jordan):
        before = counts()
        r = self.resubmit(jordan, stored_copy(14).replace("\n", "\r\n") + "  \r\n")
        assert r.status_code in (400, 409, 422) and counts() == before

    def test_a_real_change_makes_version_2(self, jordan):
        before = counts()
        r = self.resubmit(jordan, stored_copy(14) + " Subject to credit approval.")
        assert r.status_code == 303 and counts()["version"] == before["version"] + 1


class TestDecisionTwice:
    def decide(self, client, sid, outcome="approved", version="1", reason=None):
        data = {"outcome": outcome, "version": version}
        if reason:
            data["reason"] = reason
        return client.post(f"/review/{sid}/decision", data=data, follow_redirects=False)

    def test_second_decision_on_3_is_refused_and_the_first_stands(self, client):
        assert self.decide(client, 3).status_code == 303
        before = counts()
        second = self.decide(client, 3, "rejected", reason="Changed my mind.")
        assert second.status_code in (400, 409) and counts() == before
        with db.connect() as c:
            assert c.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == "approved"

    @pytest.mark.parametrize("sid", [6, 7, 8])
    def test_locked_items_refuse_a_direct_post(self, client, sid):
        with db.connect() as c:
            version = str(c.execute("SELECT current_version FROM submission WHERE id = ?", (sid,)).fetchone()[0])
        before = counts()
        r = self.decide(client, sid, "rejected", version=version, reason="Too late.")
        assert r.status_code in (400, 409) and counts() == before

    def test_locked_review_page_has_no_decision_form(self, client):
        assert 'action="/review/6/decision"' not in client.get("/review/6").text
        assert "Locked" in client.get("/review/6").text


class TestRejectThenResubmit:
    def test_7_keeps_the_rejected_v1_beside_the_approved_v2(self, client):
        with db.connect() as c:
            rows = c.execute("SELECT v.version_number, d.outcome FROM version v LEFT JOIN decision d "
                             "ON d.submission_id = v.submission_id AND d.version_number = v.version_number "
                             "WHERE v.submission_id = 7 ORDER BY 1").fetchall()
        assert [tuple(r) for r in rows] == [(1, "rejected"), (2, "approved")]
        assert "Audit trail" in client.get("/review/7").text

    def test_8_resubmit_adds_v2_and_leaves_the_v1_decision_untouched(self, client):
        as_role(client, "marketer", "Jordan Lee")
        with db.connect() as c:
            before = [tuple(r) for r in c.execute("SELECT * FROM decision WHERE submission_id = 8")]
        r = client.post("/resubmit/8", follow_redirects=False,
                        data={"copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
                              "notes": "", "base_version": "1",
                              "launch_date": (clock.today() + timedelta(days=30)).isoformat()})
        assert r.status_code == 303
        with db.connect() as c:
            after = [tuple(r) for r in c.execute("SELECT * FROM decision WHERE submission_id = 8")]
            assert c.execute("SELECT count(*) FROM version WHERE submission_id = 8").fetchone()[0] == 2
        assert after == before


class TestFalsePositiveFlags:
    def test_13_is_dismissed_with_a_note_visible_to_the_reviewer(self, client):
        html = client.get("/review/13").text
        assert "No open flags. Every flag on this version was dismissed." in html
        with db.connect() as c:
            assert c.execute("SELECT count(*) FROM flag_dismissal WHERE version_id IN "
                             "(SELECT id FROM version WHERE submission_id = 13)").fetchone()[0] == 1

    def test_12_flag_can_be_dismissed_live_and_shows_in_the_trail(self, client):
        before = counts()
        r = client.post("/review/12/dismiss", follow_redirects=False,
                        data={"rule_id": "R4", "version": "1", "note": "Explanatory use."})
        assert r.status_code == 303 and counts()["flag_dismissal"] == before["flag_dismissal"] + 1
        assert "Explanatory use." in client.get("/review/12").text


class TestCommentOnLockedVersion:
    def test_6_takes_a_comment_without_changing_status(self, client):
        before = counts()
        r = client.post("/review/6/comment", data={"text": "Filed for the record.", "version": "1"},
                        follow_redirects=False)
        assert r.status_code == 303 and counts()["comment"] == before["comment"] + 1
        assert "Filed for the record." in client.get("/review/6").text
        with db.connect() as c:
            assert c.execute("SELECT status FROM submission WHERE id = 6").fetchone()[0] == "approved"


class TestResetDuringReview:
    """The two-window version is a manual check (phase-7-steps.md step 13). This is its server half."""

    def test_reset_after_a_decision_puts_3_back_in_review_and_it_can_be_decided_again(self, client):
        client.post("/review/3/decision", data={"outcome": "approved", "version": "1"})
        r = client.post("/reset", data={"confirm": "reset"}, headers={"origin": "http://testserver"},
                        follow_redirects=False)
        assert r.status_code in (200, 303)
        with db.connect() as c:
            assert c.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == "in_review"
        assert client.post("/review/3/decision", data={"outcome": "approved", "version": "1"},
                           follow_redirects=False).status_code == 303


class TestViolationTheRulesMiss:
    def test_8_shows_no_phrase_flag_for_everyone_gets_a_yes(self, client):
        copy = stored_copy(8).lower()
        assert "everyone gets a yes" in copy
        html = client.get("/review/8").text
        assert FLAGS_ASSIST in html          # a clean-looking result is never read as approval

    def test_no_stored_flag_quotes_the_missed_phrase(self, client):
        with db.connect() as c:
            matched = [r[0] or "" for r in c.execute("SELECT matched_text FROM flag WHERE version_id IN "
                                                      "(SELECT id FROM version WHERE submission_id = 8)")]
        assert not any("everyone gets a yes" in m.lower() for m in matched)


MATRIX.update({
    "No flags found": TestNoFlagsFound,
    "Empty filter results": TestEmptyFilterResults,
    "Empty queue": TestEmptyQueue,
    "Missing fields, whitespace-only copy": TestMissingFieldsAndBlankCopy,
    "Very long copy": TestVeryLongCopy,
    "Launch in the past": TestLaunchInThePast,
    "Launch within 2 business days": TestLaunchWithinTwoBusinessDays,
    "Resubmit without changes": TestResubmitWithoutChanges,
    "Double-click a decision; decide on an already-decided version": TestDecisionTwice,
    "Reject then resubmit, history preserved": TestRejectThenResubmit,
    "Flag false positive: dismissed / live": TestFalsePositiveFlags,
    "Comment on a locked version": TestCommentOnLockedVersion,
    "Reset during an in-progress review": TestResetDuringReview,
    "Violation the rules miss": TestViolationTheRulesMiss,
})


# ---- the doc and the tests cannot drift apart ----

def doc_rows(text=None):
    """First cell of every data row in seed-data.md section 3."""
    text = DOC.read_text() if text is None else text
    section = re.search(r"^## 3\..*?(?=^## 4\.)", text, re.S | re.M).group(0)
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [r.split("|")[1].strip() for r in rows[2:]]        # skip the header and the divider


def missing_rows(rows, matrix):
    return [r for r in rows if r not in matrix]


def test_every_row_of_the_doc_has_a_test_class():
    assert missing_rows(doc_rows(), MATRIX) == []


def test_every_test_class_maps_to_a_row_of_the_doc():
    assert sorted(set(MATRIX) - set(doc_rows())) == []


def test_the_doc_has_the_rows_we_expect():
    assert len(doc_rows()) == 14


def test_a_new_doc_row_without_a_test_is_caught():
    doc = DOC.read_text().replace("| Violation the rules miss |", "| Brand new edge |\n| Violation the rules miss |")
    assert missing_rows(doc_rows(doc), MATRIX) == ["Brand new edge"]


def test_every_class_is_listed_in_the_module_docstring_table():
    for cls in MATRIX.values():
        assert cls.__name__ in __doc__
