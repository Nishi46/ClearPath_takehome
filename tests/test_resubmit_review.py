import re
from datetime import timedelta

import pytest

from app import clock, db

LAUNCH = (clock.today() + timedelta(days=30)).isoformat()


def resubmit_14(client, **over):
    client.cookies.set("role", "marketer")
    client.cookies.set("marketer", "Jordan Lee")
    data = {"copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.", "notes": "Fixed.",
            "launch_date": LAUNCH, "base_version": "1"}
    data.update(over)
    r = client.post("/resubmit/14", data=data, follow_redirects=False)
    assert r.status_code == 303
    client.cookies.set("role", "reviewer")


def decide(client, sid, version, outcome, reason="Because."):
    return client.post("/review/%d/decision" % sid, data={"outcome": outcome, "version": str(version),
                                                          "reason": reason}, follow_redirects=False)


def row_for(html, sid):
    return re.search(r'<tr[^>]*>(?:(?!</tr>).)*/review/%d[?"](?:(?!</tr>).)*</tr>' % sid, html, re.S).group(0)


def test_queue_shows_the_resubmission_as_in_review(client):
    resubmit_14(client, launch_date=(clock.today() + timedelta(days=1)).isoformat())
    row = row_for(client.get("/").text, 14)
    assert "In review" in row and "Changes requested" not in row
    assert re.search(r">\s*2\s*<", row)
    assert "Rush" in row or "rush" in row  # urgency recomputed from the new launch date


def test_status_filter_moves_the_item(client):
    assert "/review/14" in client.get("/?status=changes_requested").text
    resubmit_14(client)
    assert "/review/14" not in client.get("/?status=changes_requested").text
    assert "/review/14" in client.get("/?status=in_review").text


def test_review_page_marks_the_resubmission(client):
    resubmit_14(client)
    html = client.get("/review/14").text
    assert 'name="outcome"' in html  # v2 is decidable
    assert "history-resubmitted" in html and "version-resubmitted" in html
    texts = re.findall(r'<li class="history-\w+">(.*?)<', html)
    assert [t.strip() for t in texts][:2] == ["v1 submitted by Jordan Lee", "v1 changes requested by Alex Rivera"]
    assert "v2 submitted by Jordan Lee" in html
    # v1 stays read-only with its banner
    old = client.get("/review/14?v=1").text
    assert "Changes requested by Alex Rivera" in old and 'name="outcome"' not in old


def test_v1_has_no_resubmitted_marker_and_unresubmitted_items_have_none(client):
    html = client.get("/review/14").text
    assert "history-resubmitted" not in html and "version-resubmitted" not in html


def test_the_loop_runs_more_than_once_until_approved(client):
    resubmit_14(client)
    assert decide(client, 14, 2, "changes_requested").status_code == 303
    client.cookies.set("role", "marketer")
    assert "Changes requested" in client.get("/mine").text
    resubmit_14(client, copy="A third, different draft of the copy.", base_version="2")
    assert decide(client, 14, 3, "approved").status_code == 303
    with db.connect() as c:
        assert c.execute("SELECT status, current_version FROM submission WHERE id = 14").fetchone()[:] == ("approved", 3)
    client.cookies.set("role", "marketer")
    assert "approved and locked" in client.get("/resubmit/14").text
    assert "Done (" in client.get("/mine").text


def test_late_decision_on_v1_is_refused(client):
    resubmit_14(client)
    assert decide(client, 14, 1, "approved").status_code == 409


@pytest.mark.parametrize("path", ["/review/14", "/review/14?v=1"])
def test_pages_are_no_store(client, path):
    resubmit_14(client)
    assert client.get(path).headers["cache-control"] == "no-store"
