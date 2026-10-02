import re

import pytest

from app import db, queue
from app.review import dismissals_view, when_text


def section(html):
    return re.search(r'<section class="flags-pane".*?</section>', html, re.S).group(0)


def text_of(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


@pytest.mark.parametrize("sid", [6, 10, 11])
def test_clean_items_say_no_flags_detected_and_not_a_guarantee(client, sid):
    s = section(client.get(f"/review/{sid}").text)
    assert "Flags (0)" in s and "No flags detected." in s
    assert "not a guarantee of compliance" in s and "A reviewer still reads the copy" in s
    assert 'class="flag-empty"' in s and "Dismissed" not in s


def test_flagged_items_do_not_show_the_empty_state(client):
    for sid in (1, 3, 8):
        assert "No flags detected" not in section(client.get(f"/review/{sid}").text)


def test_item_13_shows_the_dismissal_not_an_open_flag(client):
    html = client.get("/review/13").text
    s = section(html)
    assert "Flags (0)" in s and "No open flags." in s and "No flags detected" not in s
    assert "Dismissed (1)" in s and "R1" in s and "dismissed by Alex Rivera" in s
    assert "False positive. The phrase appears in a sentence" in s
    assert "<mark" not in html
    assert 'class="flag-card"' not in s
    with db.connect() as c:
        assert [r for r in queue.list_queue(c) if r["id"] == 13][0]["flag_count"] == 0


def test_dismissal_note_is_escaped(client):
    with db.connect() as c:
        c.execute("UPDATE flag_dismissal SET note = ?, dismissed_by = ?",
                  ("<script>alert(1)</script>\nline two <b>x</b>", "<i>Mallory</i>"))
    s = client.get("/review/13").text
    assert "<script>alert" not in s and "<b>x</b>" not in s and "<i>Mallory" not in s
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in s and "&lt;i&gt;Mallory&lt;/i&gt;" in s


def test_dismissing_a_rule_hides_all_its_occurrences(client):
    with db.connect() as c:
        vid = c.execute("SELECT id FROM version WHERE submission_id = 8 AND version_number = 1").fetchone()[0]
        c.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                  " VALUES (?, 'R6', 'ok', 'Alex Rivera', '2026-10-01T10:00:00Z')", (vid,))
    html = client.get("/review/8").text
    s = section(html)
    assert "R6: " not in re.sub(r"Dismissed.*", "", s, flags=re.S)        # no open R6 card
    assert "Dismissed (1)" in s
    assert not any("R6" in h for h in re.findall(r'<li class="flag-card">.*?<h3>(.*?)</h3>', s, re.S))
    assert "Flags (2)" in s                                              # R1 and R4 remain


def test_dismissal_of_a_removed_rule_does_not_crash(client):
    with db.connect() as c:
        c.execute("UPDATE flag_dismissal SET rule_id = 'R99'")
    s = client.get("/review/13").text
    assert "Rule no longer available" in s


def test_malformed_dismissal_time_falls_back(client):
    with db.connect() as c:
        c.execute("UPDATE flag_dismissal SET created_at = 'not a time'")
    assert "unknown time" in client.get("/review/13").text


@pytest.mark.parametrize("sid", range(1, 15))
def test_both_fixed_notes_on_every_page(client, sid):
    s = section(client.get(f"/review/{sid}").text)
    assert "Flags assist the reviewer. They never decide." in s
    assert "Rules are illustrative, not legal advice." in s


@pytest.mark.parametrize("sid", [1, 6, 13])
def test_fixed_text_never_recommends_a_decision(client, sid):
    s = section(client.get(f"/review/{sid}").text)
    fixed = " ".join(re.findall(r'<p class="flag-(?:assist-note|empty)"[^>]*>(.*?)</p>', s, re.S))
    assert fixed and not re.search(r"approv|recommend|reject", fixed, re.I)


def test_when_text():
    assert when_text("2026-10-02T14:05:09Z") == "Oct 2, 2026 14:05 UTC"
    for bad in (None, "", "2026-10-02", "2026-13-40T00:00:00Z", 5, "x" * 1000):
        assert when_text(bad) == "unknown time"


def test_dismissals_view_is_plain_text(client):
    with db.connect() as c:
        from app.review import load_review
        data = load_review(c, 13)
    v = dismissals_view(data)
    assert len(v) == 1 and set(v[0]) == {"rule_id", "name", "by", "when", "note", "snippet"}
