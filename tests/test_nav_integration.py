import re
from datetime import timedelta

import pytest

from app import clock, db


def nav_links(html):
    nav = re.search(r'<nav class="site-nav".*?</nav>', html, re.S).group(0)
    return re.findall(r'<a href="([^"]+)">([^<]+)</a>', nav)


def test_reviewer_nav(client):
    assert nav_links(client.get("/").text) == [("/", "Queue"), ("/submit", "Submit")]


def test_marketer_nav(mclient):
    assert nav_links(mclient.get("/").text) == [("/", "Queue"), ("/mine", "My submissions"), ("/submit", "Submit")]


@pytest.mark.parametrize("role", ["reviewer", "marketer"])
@pytest.mark.parametrize("page", ["/", "/submit", "/mine", "/review/1", "/reset/confirm"])
def test_every_nav_target_resolves_for_both_roles(client, role, page):
    client.cookies.set("role", role)
    for href, _ in nav_links(client.get(page).text):
        assert client.get(href).status_code == 200


def test_role_switch_lands_where_step_10_says(client):
    assert client.post("/role", data={"role": "marketer"}, follow_redirects=False).headers["location"] == "/mine"
    assert client.post("/role", data={"role": "reviewer"}, follow_redirects=False).headers["location"] == "/"


def submit(client, title="Urgent tomorrow", days=1, **over):
    day = clock.today() + timedelta(days=days)
    data = {"title": title, "product": "loan", "channel": "email", "copy": "Hello there.",
            "launch_date": day.isoformat()}
    data.update(over)
    return client.post("/submit", data=data, follow_redirects=False)


def first_row_id(client):
    return int(re.search(r'/review/(\d+)', client.get("/").text.split("<tbody")[1]).group(1))


def queue_ids(client):
    return [int(i) for i in re.findall(r'/review/(\d+)', client.get("/").text.split("<tbody")[1])]


def expected_ids():
    with db.connect() as c:
        return [r[0] for r in c.execute("SELECT id FROM submission ORDER BY launch_date, created_at, id")]


def test_a_new_submission_is_in_the_queue_with_its_submitter(mclient):
    assert submit(mclient).status_code == 303
    with db.connect() as c:
        sid = c.execute("SELECT max(id) FROM submission").fetchone()[0]
    mclient.cookies.set("role", "reviewer")
    html = mclient.get("/").text
    assert "Maya Chen" in html and "Urgent tomorrow" in html
    assert queue_ids(mclient) == expected_ids()  # the default launch-date order, immediately
    assert sid in queue_ids(mclient)


def test_an_item_launching_tomorrow_leads_every_later_launch(mclient):
    submit(mclient)
    with db.connect() as c:
        sid, tomorrow = c.execute("SELECT id, launch_date FROM submission ORDER BY id DESC LIMIT 1").fetchone()
        later = [r[0] for r in c.execute("SELECT id FROM submission WHERE launch_date > ?", (tomorrow,))]
    ids = queue_ids(mclient)
    assert all(ids.index(sid) < ids.index(i) for i in later)
    html = mclient.get("/").text
    assert re.search(r"Urgent tomorrow.*?(Rush|rush)", html.split("<tbody")[1], re.S)


def test_a_far_launch_sorts_last(mclient):
    submit(mclient, title="Far away", days=400)
    with db.connect() as c:
        sid = c.execute("SELECT max(id) FROM submission").fetchone()[0]
    assert queue_ids(mclient)[-1] == sid


def test_switching_marketer_changes_mine_only(mclient):
    base = mclient.get("/").text
    mine_maya = mclient.get("/mine").text
    mclient.cookies.set("marketer", "Jordan Lee")
    assert mclient.get("/").text.replace("Viewing as", "") == base or re.sub(r"\s+", " ", mclient.get("/").text) == re.sub(r"\s+", " ", base)
    assert mclient.get("/mine").text != mine_maya


def test_the_empty_queue_links_to_a_working_submit(client):
    with db.connect() as c:
        for t in ("flag_dismissal", "comment", "decision", "flag", "version", "submission"):
            c.execute("DELETE FROM %s" % t)
        c.commit()
    html = client.get("/").text
    assert 'href="/submit"' in html and "No submissions yet" in html
    assert client.get("/submit").status_code == 200
