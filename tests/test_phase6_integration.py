import re
from html import unescape

import pytest

from app import db, mine, queue, rules
from app.routes import pages

TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")
SEVERITY_LETTER = {"high": "H", "medium": "M", "low": "L"}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def dump():
    with db.connect() as c:
        return {t: [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)] for t in TABLES}


def as_reviewer(client):
    client.cookies.set("role", "reviewer")
    return client


def as_marketer(client, name):
    client.cookies.set("role", "marketer")
    client.cookies.set("marketer", name)
    return client


def queue_cell(client, sid):
    """The Flags cell of the queue row for one submission: (visible text, words for screen readers)."""
    html = client.get("/").text
    row = re.split(r'href="/review/%d(?=["?])' % sid, html)[1].split('href="/review/')[0]
    return (re.search(r'<span class="flag-count">(\d+)</span>', row).group(1) if "flag-count" in row else None,
            unescape(re.search(r'<span class="visually-hidden">(.*?)</span>', row[row.index("flags"):]).group(1)),
            re.search(r'class="sev sev-(\w+)"', row))


def dismiss(client, sid, rule, version=1, note="Not applicable here."):
    return client.post("/review/%d/dismiss" % sid, data={"rule_id": rule, "version": str(version), "note": note},
                       follow_redirects=False)


def open_flags(sid):
    with db.connect() as c:
        return c.execute(
            "SELECT DISTINCT f.rule_id, f.severity FROM flag f JOIN version v ON v.id = f.version_id"
            " JOIN submission s ON s.id = v.submission_id AND v.version_number = s.current_version"
            " WHERE s.id = ? AND NOT EXISTS (SELECT 1 FROM flag_dismissal x WHERE x.version_id = f.version_id"
            " AND x.rule_id = f.rule_id)", (sid,)).fetchall()


# ---- the Flags column (queue and My submissions) ----

def test_12_queue_flag_cell_goes_from_one_flag_to_none(client):
    as_reviewer(client)
    count, words, sev = queue_cell(client, 12)
    assert count == "1" and words == "1 rule flagged, highest severity Medium" and sev.group(1) == "medium"
    assert dismiss(client, 12, "R4").status_code == 303
    count, words, sev = queue_cell(client, 12)
    assert count == "0" and words == "No flags" and sev is None


def test_dismissing_one_of_several_drops_the_count_by_one_and_updates_the_letter(client):
    as_reviewer(client)
    before = open_flags(1)
    assert len(before) >= 3
    top_rule = min(before, key=lambda r: (SEVERITY_RANK[r["severity"]], r["rule_id"]))
    count, words, sev = queue_cell(client, 1)
    assert int(count) == len(before) and sev.group(1) == top_rule["severity"]
    assert dismiss(client, 1, top_rule["rule_id"]).status_code == 303
    after = open_flags(1)
    expected_top = min(SEVERITY_RANK[r["severity"]] for r in after)
    count, words, sev = queue_cell(client, 1)
    assert int(count) == len(before) - 1 == len(after)
    assert SEVERITY_RANK[sev.group(1)] == expected_top
    assert ("highest severity %s" % sev.group(1).capitalize()) in words


def test_my_submissions_counts_only_open_flags(client):
    as_reviewer(client)
    as_marketer(client, "Maya Chen")
    with db.connect() as c:
        before = {r["id"]: r["flag_count"] for r in mine.list_mine(c, "Maya Chen")}
    assert before[12] == 1
    as_reviewer(client)
    dismiss(client, 12, "R4")
    as_marketer(client, "Maya Chen")
    with db.connect() as c:
        after = {r["id"]: r["flag_count"] for r in mine.list_mine(c, "Maya Chen")}
    assert after[12] == 0 and {k: v for k, v in after.items() if k != 12} == {k: v for k, v in before.items() if k != 12}
    assert "0 open flags" in client.get("/mine").text


# ---- the marketer's feedback panel ----

def test_jordan_sees_the_snippet_comments_and_reason_on_14_and_a_new_comment_with_its_time(client):
    as_marketer(client, "Jordan Lee")
    html = client.get("/review/14").text
    panel = html.split('id="feedback-heading"')[1].split("</section>")[0]
    for rid in ("R2", "R3", "R7"):
        assert unescape(rules.describe({"rule_id": rid})["snippet"]) in unescape(panel)
    assert "Three required disclosures are missing" in panel
    as_reviewer(client)
    assert client.post("/review/14/comment", data={"text": "One more thing: check the footer.", "version": "1"},
                       follow_redirects=False).status_code == 303
    as_marketer(client, "Jordan Lee")
    panel = client.get("/review/14").text.split('id="feedback-heading"')[1].split("</section>")[0]
    assert "One more thing: check the footer." in panel and re.search(r"Alex Rivera</strong> &middot; \w+ \d+, \d{4} \d\d:\d\d UTC", panel)


# ---- resubmission does not carry dismissals ----

def test_a_dismissal_stays_with_v1_and_v2_flags_are_computed_fresh(client):
    as_reviewer(client)
    assert dismiss(client, 12, "R4").status_code == 303
    assert client.post("/review/12/decision", data={"outcome": "changes_requested", "version": "1",
                                                    "reason": "Reword the paragraph."},
                       follow_redirects=False).status_code == 303
    with db.connect() as c:
        copy = c.execute("SELECT copy FROM version WHERE submission_id = 12 AND version_number = 1").fetchone()[0]
    with db.connect() as c:
        launch = c.execute("SELECT launch_date FROM submission WHERE id = 12").fetchone()[0]
    as_marketer(client, "Maya Chen")
    r = client.post("/resubmit/12", data={"copy": copy + "\n\nThanks for reading.", "notes": "",
                                          "base_version": "1", "launch_date": launch}, follow_redirects=False)
    assert r.status_code == 303
    as_reviewer(client)
    v2 = client.get("/review/12").text
    assert "Flags (1)" in v2 and "R4:" in v2.split("Dismissed (")[0] and "Dismissed (" not in v2
    assert 'name="rule_id" value="R4"' in v2                      # open again, dismissable again on v2
    v1 = client.get("/review/12?v=1").text
    assert "Dismissed (1)" in v1 and "Not applicable here." in v1
    trail = v2.split('class="audit-trail"')[1]
    kinds = re.findall(r'<li class="trail-item trail-(\w+)">', trail)
    assert kinds == ["version", "dismissal", "decision", "version"]
    assert "Flag dismissed: R4" in trail and trail.index("Flag dismissed") < trail.index("v2 submitted")


# ---- approving after a dismissal ----

def test_an_approved_item_keeps_its_dismissal_and_still_takes_comments(client):
    as_reviewer(client)
    assert dismiss(client, 12, "R4", note="Quoted phrase, fine.").status_code == 303
    assert client.post("/review/12/decision", data={"outcome": "approved", "version": "1"},
                       follow_redirects=False).status_code == 303
    html = client.get("/review/12").text
    assert "Dismissed (1)" in html and "Quoted phrase, fine." in html
    assert 'class="flag-dismiss"' not in html and "/review/12/dismiss" not in html   # no dismiss form
    assert 'id="comment-form"' in html and "/review/12/comment" in html              # comments still allowed
    trail = html.split('class="audit-trail"')[1]
    assert trail.index("Flag dismissed: R4") < trail.index("Approved")
    assert dismiss(client, 12, "R4").status_code == 409                               # and the server agrees
    assert client.post("/review/12/comment", data={"text": "Filed.", "version": "1"},
                       follow_redirects=False).status_code == 303


# ---- reset ----

def test_reset_removes_new_dismissals_and_comments_and_restores_the_seed_exactly(client):
    start = dump()
    as_reviewer(client)
    dismiss(client, 12, "R4")
    dismiss(client, 1, "R1")
    client.post("/review/14/comment", data={"text": "extra", "version": "1"})
    client.post("/review/6/comment", data={"text": "post decision note", "version": "1"})
    assert dump() != start
    assert client.post("/reset", data={"confirm": "reset"}, follow_redirects=False).status_code == 303
    assert dump() == start
    assert len(dump()["flag_dismissal"]) == 1 and dump()["flag_dismissal"][0][2] == "R1"      # #13's, back
    html = client.get("/review/13").text
    assert "Dismissed (1)" in html and "False positive" in html
    pages.reset_cooldown.clear()
    from app import audit
    with db.connect() as c:
        for sid in range(1, 15):
            events = audit.load_trail(c, sid)
            expected = sum(c.execute(q, (sid,)).fetchone()[0] for q in (
                "SELECT count(*) FROM version WHERE submission_id = ?",
                "SELECT count(*) FROM decision WHERE submission_id = ?",
                "SELECT count(*) FROM comment WHERE submission_id = ?",
                "SELECT count(*) FROM flag_dismissal WHERE version_id IN (SELECT id FROM version WHERE submission_id = ?)"))
            assert len(events) == expected
