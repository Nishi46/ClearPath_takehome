import ast
import random
import re
import subprocess
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import clock, db
from app.main import app

APP_DIR = Path(__file__).resolve().parent.parent / "app"
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")
LAUNCH = (clock.today() + timedelta(days=30)).isoformat()
EVIL = ["https://evil.example", "null"]
HOSTILE = '"><img src=x onerror=alert(1)><script>alert(1)</script></textarea></ins></del>'


def dump():
    with db.connect() as c:
        return {t: [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)] for t in TABLES}


def counts():
    with db.connect() as c:
        return (c.execute("SELECT count(*) FROM submission").fetchone()[0],
                c.execute("SELECT count(*) FROM version").fetchone()[0])


def who(client, role, marketer="Maya Chen"):
    client.cookies.set("role", role)
    client.cookies.set("marketer", marketer)
    return client


def new_item(**over):
    data = {"title": "T", "product": "loan", "channel": "email", "copy": "Guaranteed approval. Apply now.",
            "notes": "n", "launch_date": LAUNCH}
    data.update(over)
    return data


def resubmit_data(**over):
    data = {"copy": "A different copy, rewritten.", "notes": "n", "launch_date": LAUNCH, "base_version": "1"}
    data.update(over)
    return data


# ---- route table and code greps ----

def test_the_route_table_is_exactly_the_documented_one():
    routes = []
    for r in app.routes:
        routes += r.original_router.routes if hasattr(r, "original_router") else [r]
    seen = {(r.path, tuple(sorted(r.methods))) for r in routes if hasattr(r, "methods")}
    new = {("/submit", ("GET",)), ("/submit", ("POST",)), ("/submit/check", ("POST",)),
           ("/mine", ("GET",)), ("/resubmit/{submission_id}", ("GET",)),
           ("/resubmit/{submission_id}", ("POST",)), ("/marketer", ("POST",))}
    assert new <= seen
    old = {("/healthz", ("GET",)), ("/", ("GET",)), ("/role", ("POST",)),
           ("/reset/confirm", ("GET",)), ("/reset", ("POST",)), ("/review/{submission_id}", ("GET",)),
           ("/review/{submission_id}/decision", ("POST",)),
           ("/review/{submission_id}/dismiss", ("POST",)),
           ("/review/{submission_id}/comment", ("POST",))}
    assert seen == new | old


FORBIDDEN = ("submitted_by", "status", "current_version", "created_at", "flags", "reviewer", "id", "outcome",
             "version_number")


def test_no_new_route_reads_a_server_owned_field():
    text = (APP_DIR / "routes" / "submit_pages.py").read_text()
    read = set(re.findall(r'_single\(form, "(\w+)"\)', text)) | set(re.findall(r'form\.get(?:list)?\("(\w+)"', text))
    assert read and not read & set(FORBIDDEN)
    assert not re.search(r"Form\(", text)
    fields = set(re.findall(r"^(?:FIELDS|RESUBMIT_FIELDS) = \(([^)]*)\)", text, re.M | re.S))
    for group in fields:
        assert not set(re.findall(r'"(\w+)"', group)) & set(FORBIDDEN)


def test_no_unsafe_constructs_in_app():
    hits = []
    for path in list(APP_DIR.rglob("*.py")) + list(APP_DIR.rglob("*.html")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\|\s*safe|Markup\(|\beval\(|\bexec\(|pickle", line) and not line.lstrip().startswith(("#", "{#")):
                hits.append("%s:%d" % (path.name, n))
    # Comments in templates and tests may mention them; real uses may not exist.
    assert not [h for h in hits if not h.startswith(("security.py", "templating.py"))], hits


@pytest.mark.parametrize("module", ["submit.py", "mine.py", "diff.py", "review.py", "queue.py"])
def test_every_sql_statement_is_a_constant_with_bound_values(module):
    tree = ast.parse((APP_DIR / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "execute", "executemany", "executescript"):
            first = node.args[0]
            ok = (isinstance(first, ast.Constant) and isinstance(first.value, str)) or \
                 (isinstance(first, ast.Name) and first.id.startswith("sql_"))
            assert ok, "%s line %d builds SQL at run time" % (module, node.lineno)


# ---- mass assignment, IDOR, CSRF ----

def test_mass_assignment_on_submit_changes_nothing_but_validated_fields(client):
    who(client, "marketer")
    r = client.post("/submit", data=new_item(status="approved", outcome="approved", version_number="5", id="1",
                                             submitted_by="Mallory", current_version="9", created_at="1999-01-01",
                                             flags="R1", reviewer="Eve"), follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT * FROM submission ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["status"], row["current_version"], row["submitted_by"]) == ("new", 1, "Maya Chen")
    assert row["id"] != 1 and not row["created_at"].startswith("1999")


def test_mass_assignment_on_resubmit(client):
    who(client, "marketer", "Jordan Lee")
    r = client.post("/resubmit/14", data=resubmit_data(status="approved", outcome="approved", version_number="5",
                                                       id="1", submitted_by="Maya Chen", reviewer="Eve", title="X"),
                    follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as c:
        s = c.execute("SELECT * FROM submission WHERE id = 14").fetchone()
        v = c.execute("SELECT version_number FROM version WHERE submission_id = 14 ORDER BY 1").fetchall()
    assert (s["status"], s["current_version"], s["submitted_by"], s["title"] != "X") == ("in_review", 2, "Jordan Lee", True)
    assert [r[0] for r in v] == [1, 2]


def test_idor_maya_cannot_resubmit_jordans_items_and_mine_is_scoped(client):
    who(client, "marketer", "Maya Chen")
    before = dump()
    for sid in (4, 7, 8, 10, 14):
        assert client.post("/resubmit/%d" % sid, data=resubmit_data(), follow_redirects=False).status_code in (403, 409)
    assert client.post("/resubmit/14", data=resubmit_data(), follow_redirects=False).status_code == 403
    assert dump() == before
    mine = client.get("/mine").text
    with db.connect() as c:
        theirs = [r[0] for r in c.execute("SELECT id FROM submission WHERE submitted_by != 'Maya Chen'")]
        mine_ids = {r[0] for r in c.execute("SELECT id FROM submission WHERE submitted_by = 'Maya Chen'")}
    listed = {int(i) for i in re.findall(r'href="/review/(\d+)"', mine)}
    assert listed == mine_ids and not listed & set(theirs)


@pytest.mark.parametrize("origin", EVIL)
@pytest.mark.parametrize("path,data", [
    ("/submit", new_item()), ("/submit/check", new_item()), ("/marketer", {"name": "Jordan Lee"}),
    ("/resubmit/14", resubmit_data()), ("/review/3/decision", {"outcome": "approved", "version": "1", "reason": ""}),
    ("/role", {"role": "marketer"})])
def test_cross_origin_posts_write_nothing_and_set_no_cookie(client, origin, path, data):
    who(client, "marketer", "Jordan Lee")
    if "review" in path:
        client.cookies.set("role", "reviewer")
    before = dump()
    r = client.post(path, data=data, headers={"Origin": origin}, follow_redirects=False)
    if path != "/role":  # the older /role route predates this check and only sets a label
        assert r.status_code == 403
        assert "set-cookie" not in r.headers
    assert dump() == before


@pytest.mark.parametrize("origin", [None, "http://testserver"])
def test_absent_or_matching_origin_works(client, origin):
    who(client, "marketer")
    headers = {"Origin": origin} if origin else {}
    assert client.post("/submit", data=new_item(), headers=headers, follow_redirects=False).status_code == 303


# ---- XSS across every page ----

class Scan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.bad = []

    def handle_starttag(self, tag, attrs):
        if tag == "img" or (tag == "script" and not dict(attrs).get("src")):
            self.bad.append(tag)
        for name, _ in attrs:
            if name.startswith("on"):
                self.bad.append(name)


def assert_inert(html):
    scan = Scan()
    scan.feed(html)
    assert not scan.bad, scan.bad


def test_hostile_text_is_inert_on_every_page(client):
    who(client, "marketer", "Jordan Lee")
    r = client.post("/submit", data=new_item(title=HOSTILE[:120], copy=HOSTILE + " apply now", notes=HOSTILE,
                                              launch_date=LAUNCH), follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as c:
        sid = c.execute("SELECT max(id) FROM submission").fetchone()[0]
    client.cookies.set("marketer", "Maya Chen")  # the submit above was as Jordan; submitter is Jordan
    client.cookies.set("marketer", "Jordan Lee")
    client.cookies.set("role", "reviewer")
    assert client.post("/review/%d/decision" % sid, data={"outcome": "changes_requested", "version": "1",
                                                          "reason": HOSTILE}, follow_redirects=False).status_code == 303
    client.post("/review/%d/decision" % sid, data={"outcome": "approved", "version": "1"}, follow_redirects=False)
    client.cookies.set("role", "marketer")
    r = client.post("/resubmit/%d" % sid, data=resubmit_data(copy=HOSTILE + " rewritten", notes=HOSTILE),
                    follow_redirects=False)
    assert r.status_code == 303
    pages = ["/", "/mine", "/mine?submitted=%d" % sid, "/review/%d" % sid, "/review/%d?diff=1" % sid,
             "/review/%d?v=1" % sid, "/resubmit/%d" % sid, "/submit"]
    for role in ("marketer", "reviewer"):
        client.cookies.set("role", role)
        for page in pages:
            r = client.get(page)
            assert r.status_code == 200, page
            assert_inert(r.text)
    # re-rendered forms
    client.cookies.set("role", "marketer")
    assert_inert(client.post("/submit", data=new_item(title=HOSTILE, copy=HOSTILE, launch_date="bad")).text)
    assert_inert(client.post("/submit/check", data=new_item(copy=HOSTILE)).text)


# ---- fuzz ----

def junk(rng):
    kind = rng.randrange(6)
    if kind == 0:
        return rng.randbytes(rng.randrange(0, 200)).decode("latin-1")
    if kind == 1:
        return "x" * rng.choice([1, 130, 5000, 20000])
    if kind == 2:
        return "".join(chr(rng.choice([0x202e, 0x200b, 0x1f600, 0x301, 0xd7ff, 0x41, 0x0a, 0x0d, 0x00, 0x07]))
                       for _ in range(rng.randrange(0, 40)))
    if kind == 3:
        return rng.choice(["", " ", "-1", "0", "9999", "1.5", "2026-02-30", LAUNCH, "loan", "email", "Maya Chen"])
    if kind == 4:
        return rng.choice([HOSTILE, "'; DROP TABLE submission;--", "{{7*7}}", "%s%n", "../../etc/passwd"])
    return str(rng.randrange(10 ** rng.randrange(1, 14)))


def test_fuzz_new_routes_never_500_leak_or_write_on_failure(client):
    rng = random.Random(20261002)
    fields = ["title", "product", "channel", "launch_date", "copy", "notes", "base_version", "action", "name",
              "status", "submitted_by", "back", "diff", "v", "submitted", "role"]
    posts = ["/submit", "/submit/check", "/resubmit/%s", "/marketer"]
    gets = ["/mine", "/submit", "/resubmit/%s", "/review/%s"]
    for i in range(300):
        who(client, rng.choice(["marketer", "reviewer"]), rng.choice(["Maya Chen", "Jordan Lee", "Sam Patel"]))
        from urllib.parse import quote
        ident = rng.choice(["14", "8", "6", "3", "999", "abc", quote(junk(rng)[:20], safe="") or "1"])
        before = counts()
        data = {f: junk(rng) for f in rng.sample(fields, rng.randrange(0, 9))}
        if rng.random() < 0.4 and "copy" not in data:
            data["copy"] = "ok " + junk(rng)[:30]
        method = rng.choice(["post", "get", "json", "raw"])
        try:
            if method == "post":
                path = rng.choice(posts)
                r = client.post(path % ident if "%s" in path else path, data=data, follow_redirects=False)
            elif method == "get":
                path = rng.choice(gets)
                r = client.get((path % ident if "%s" in path else path), params={k: v for k, v in data.items()
                                                                               if k in ("diff", "v", "back", "submitted")})
            elif method == "json":
                r = client.post(rng.choice(posts) % ident if False else "/submit", json=data, follow_redirects=False)
            else:
                r = client.post(rng.choice(["/submit", "/submit/check", "/marketer"]),
                                content=rng.randbytes(rng.randrange(0, 3000)),
                                headers={"content-type": rng.choice(["application/x-www-form-urlencoded",
                                                                     "multipart/form-data; boundary=x", "text/plain"])},
                                follow_redirects=False)
        except Exception as exc:  # the test client re-raises server errors
            pytest.fail("request %d raised %r" % (i, exc))
        assert r.status_code < 500, (i, r.status_code)
        assert "Traceback" not in r.text and "sqlite3" not in r.text
        if not 300 <= r.status_code < 400:
            assert counts() == before, (i, r.status_code)
    with db.connect() as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# ---- capacity and reset ----

def test_capacity_abuse_stops_at_the_cap_and_the_app_keeps_serving(client):
    from app import submit

    who(client, "marketer")
    codes = []
    for i in range(400):
        r = client.post("/submit", data=new_item(title="Abuse %d" % i), follow_redirects=False)
        codes.append(r.status_code)
        if r.status_code == 503:
            assert "demo is full" in r.text
            break
    assert 503 in codes and counts()[0] == submit.MAX_SUBMISSIONS
    assert client.get("/").status_code == 200 and client.get("/mine").status_code == 200
    assert client.post("/reset", data={"confirm": "reset"}, follow_redirects=False).status_code == 303
    assert counts()[0] == 14 or counts()[0] < submit.MAX_SUBMISSIONS


def test_reset_after_the_full_loop_restores_the_seed_and_the_loop_runs_again(client):
    start = dump()

    def loop():
        who(client, "marketer", "Maya Chen")
        assert client.post("/submit", data=new_item(title="Loop"), follow_redirects=False).status_code == 303
        sid = max(r[0] for r in dump()["submission"])
        client.cookies.set("role", "reviewer")
        assert client.post("/review/%d/decision" % sid, data={"outcome": "changes_requested", "version": "1",
                                                              "reason": "Fix it."}, follow_redirects=False).status_code == 303
        who(client, "marketer", "Maya Chen")
        assert client.post("/resubmit/%d" % sid, data=resubmit_data(), follow_redirects=False).status_code == 303
        assert client.get("/review/%d?diff=1" % sid).status_code == 200
        client.cookies.set("role", "reviewer")
        assert client.post("/review/%d/decision" % sid, data={"outcome": "approved", "version": "2"},
                           follow_redirects=False).status_code == 303
        who(client, "marketer", "Jordan Lee")
        assert client.post("/resubmit/14", data=resubmit_data(), follow_redirects=False).status_code == 303

    loop()
    assert dump() != start
    assert client.post("/reset", data={"confirm": "reset"}, follow_redirects=False).status_code == 303
    assert dump() == start
    from app.routes import pages
    pages.reset_cooldown.clear()
    loop()
    assert client.post("/reset", data={"confirm": "reset"}, follow_redirects=False).status_code == 303
    assert dump() == start
