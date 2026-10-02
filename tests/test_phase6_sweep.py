import ast
import random
import re
import string
from pathlib import Path

import pytest

from app import db
from app.main import app

APP_DIR = Path(__file__).resolve().parent.parent / "app"
TABLES = ("submission", "version", "flag", "flag_dismissal", "decision", "comment")
EVIL_ORIGINS = ["https://evil.example", "null", "https://testserver.evil.example"]
REVIEWER = "Alex Rivera"
# (submission id, current version, a rule that fired on it); #6 #7 #8 #13 #14 are decided.
DECIDED = [(6, 1), (7, 2), (8, 1), (13, 1), (14, 1)]


def dump():
    with db.connect() as c:
        return {t: [tuple(r) for r in c.execute("SELECT * FROM %s ORDER BY id" % t)] for t in TABLES}


def row_counts():
    with db.connect() as c:
        return {t: c.execute("SELECT count(*) FROM %s" % t).fetchone()[0] for t in TABLES}


def role(client, name):
    if name is None:
        client.cookies.clear()
    else:
        client.cookies.set("role", name)
    return client


def dismiss(client, sid=12, rule="R4", version="1", note="Quoted phrase.", **extra):
    return client.post("/review/%d/dismiss" % sid, data={"rule_id": rule, "version": version, "note": note, **extra},
                       follow_redirects=False)


def comment(client, sid=3, text="A comment.", version="1", **extra):
    return client.post("/review/%d/comment" % sid, data={"text": text, "version": version, **extra},
                       follow_redirects=False)


def version_of(sid):
    with db.connect() as c:
        return c.execute("SELECT current_version FROM submission WHERE id = ?", (sid,)).fetchone()[0]


def a_fired_rule(sid):
    with db.connect() as c:
        return c.execute("SELECT f.rule_id FROM flag f JOIN version v ON v.id = f.version_id"
                         " WHERE v.submission_id = ? ORDER BY v.version_number DESC, f.id LIMIT 1", (sid,)).fetchone()


# ---- route table ----

def test_the_phase_6_routes_are_post_only_and_everything_else_is_get():
    routes = []
    for r in app.routes:
        routes += r.original_router.routes if hasattr(r, "original_router") else [r]
    seen = {(r.path, tuple(sorted(r.methods))) for r in routes if hasattr(r, "methods")}
    assert ("/review/{submission_id}/dismiss", ("POST",)) in seen
    assert ("/review/{submission_id}/comment", ("POST",)) in seen
    assert not [p for p, m in seen if p.endswith(("/dismiss", "/comment")) and m != ("POST",)]
    review_paths = sorted(p for p, _ in seen if p.startswith("/review"))
    assert review_paths == ["/review/{submission_id}", "/review/{submission_id}/comment",
                            "/review/{submission_id}/decision", "/review/{submission_id}/dismiss"]


def test_get_never_writes_even_with_every_query_option(client):
    before = dump()
    role(client, "reviewer")
    for sid in range(1, 15):
        for q in ("", "?snippet=R1", "?snippet=R2&snippet=R3", "?diff=1&snippet=R4", "?v=1&snippet=R2",
                  "?back=%2F&snippet=R7", "?snippet=" + "A" * 3000):
            assert client.get("/review/%d%s" % (sid, q)).status_code in (200, 404)
    assert dump() == before


# ---- mass assignment ----

def test_mass_assignment_on_dismiss_stores_none_of_it(client):
    role(client, "reviewer")
    decisions_before = row_counts()["decision"]
    r = dismiss(client, dismissed_by="Mallory", reviewer="Mallory", created_at="1999-01-01T00:00:00Z", id="1",
                status="approved", outcome="approved", version_id="1", author="Mallory")
    assert r.status_code == 303
    with db.connect() as c:
        d = c.execute("SELECT d.*, v.submission_id FROM flag_dismissal d JOIN version v ON v.id = d.version_id"
                      " WHERE v.submission_id = 12").fetchone()
        assert (d["dismissed_by"], d["rule_id"], d["version_id"] != 1) == (REVIEWER, "R4", True)
        assert not d["created_at"].startswith("1999") and d["id"] != 1
        assert c.execute("SELECT status FROM submission WHERE id = 12").fetchone()[0] == "new"
        assert c.execute("SELECT count(*) FROM decision").fetchone()[0] == decisions_before


def test_mass_assignment_on_comment_stores_none_of_it(client):
    role(client, "reviewer")
    r = comment(client, text="Plain text", author="Mallory", reviewer="Mallory", created_at="1999-01-01T00:00:00Z",
                id="1", status="approved", outcome="approved", version_id="1", submission_id="9")
    assert r.status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT * FROM comment WHERE text = 'Plain text'").fetchone()
        assert (row["author"], row["submission_id"], row["rule_id"]) == (REVIEWER, 3, None)
        assert not row["created_at"].startswith("1999") and row["id"] != 1
        assert c.execute("SELECT status FROM submission WHERE id = 3").fetchone()[0] == "in_review"


# ---- role matrix ----

@pytest.mark.parametrize("who", ["marketer", None, "admin", "REVIEWER", ""])
@pytest.mark.parametrize("sid", range(1, 15))
def test_role_matrix_only_a_reviewer_writes(client, who, sid):
    role(client, who)
    fired = a_fired_rule(sid)
    before = dump()
    r1 = dismiss(client, sid, fired["rule_id"] if fired else "R1", str(version_of(sid)))
    r2 = comment(client, sid, "matrix comment", str(version_of(sid)))
    reviewer_like = who in (None, "admin", "REVIEWER", "")      # unknown or missing falls back to the reviewer default
    if who == "marketer":
        assert (r1.status_code, r2.status_code) == (403, 403) and dump() == before
    else:
        assert reviewer_like
        assert r2.status_code == 303                             # comments work on every item
        after = dump()
        assert len(after["comment"]) == len(before["comment"]) + 1
        assert after["submission"] == before["submission"] and after["decision"] == before["decision"]
        assert after["flag"] == before["flag"] and after["version"] == before["version"]


# ---- locked matrix ----

@pytest.mark.parametrize("sid, v", DECIDED)
def test_locked_items_refuse_dismissals_but_take_comments_and_keep_their_status(client, sid, v):
    role(client, "reviewer")
    before = dump()
    fired = a_fired_rule(sid)
    r = dismiss(client, sid, fired["rule_id"] if fired else "R1", str(v))
    assert r.status_code == 409 and dump() == before
    assert comment(client, sid, "after the decision", str(v)).status_code == 303
    after = dump()
    assert after["submission"] == before["submission"] and after["decision"] == before["decision"]
    assert after["flag_dismissal"] == before["flag_dismissal"]
    assert len(after["comment"]) == len(before["comment"]) + 1


# ---- CSRF ----

@pytest.mark.parametrize("origin", EVIL_ORIGINS)
def test_cross_origin_posts_are_refused_with_a_valid_cookie(client, origin):
    role(client, "reviewer")
    before = dump()
    for header in ("Origin", "Referer"):
        value = origin if header == "Origin" else origin + "/page"
        assert client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "x"},
                           headers={header: value}).status_code == 403
        assert client.post("/review/3/comment", data={"text": "x", "version": "1"},
                           headers={header: value}).status_code == 403
    assert dump() == before


# ---- XSS and injection ----

HOSTILE = [
    "<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "</textarea><b>x</b>", "{{7*7}}", "${7*7}",
    "'); DROP TABLE comment;--", "a\r\nb", "\u202Eevil\u202C", "x" * 1000, "\U0001F600" * 50,
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_text_is_stored_verbatim_and_rendered_escaped_everywhere(client, payload):
    role(client, "reviewer")
    n = dismiss(client, 12, "R4", note=payload)
    c = comment(client, 3, payload)
    assert n.status_code in (303, 422) and c.status_code in (303, 422)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM comment").fetchone()[0] >= 5          # tables survive
        assert conn.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] >= 1
    pages = [client.get("/review/12").text, client.get("/review/3").text, client.get("/").text]
    role(client, "marketer")
    client.cookies.set("marketer", "Maya Chen")
    pages += [client.get("/mine").text, client.get("/review/12").text]
    for html in pages:
        assert "<script>alert" not in html and "<img src=x" not in html and "<b>x</b>" not in html
        assert "49" not in re.findall(r'<(?:p|span) class="(?:comment-text|trail-detail|dismissal-note)">(.*?)</', html, re.S)
    if n.status_code == 303 and "\r" not in payload and payload == payload.strip():
        with db.connect() as conn:
            stored = conn.execute("SELECT note FROM flag_dismissal WHERE rule_id = 'R4' AND version_id ="
                                  " (SELECT id FROM version WHERE submission_id = 12)").fetchone()[0]
        assert stored == payload


@pytest.mark.parametrize("payload", ["<script>alert(1)</script>", "R4' OR '1'='1", "R4\r\nX-Evil: 1", "\x00", "R" * 10000,
                                     "../../etc/passwd", "{{7*7}}"])
def test_hostile_rule_id_is_refused_and_never_reflected(client, payload):
    role(client, "reviewer")
    before = dump()
    for r in (dismiss(client, 12, payload), comment(client, 12, "ok text", rule_id=payload)):
        assert r.status_code in (409, 413, 422) and "49" not in r.text
        assert payload not in r.text or payload in ("{{7*7}}",) and "{{7*7}}" not in r.text
        assert "X-Evil" not in r.headers
    assert dump() == before


def test_nul_and_control_characters_are_refused_in_every_text_field(client):
    role(client, "reviewer")
    before = dump()
    for bad in ("a\x00b", "a\x07b", "\x1b[31m"):
        assert dismiss(client, note=bad).status_code == 422
        assert comment(client, text=bad).status_code == 422
    assert dump() == before


# ---- fuzz ----

def _random_text(rng):
    alphabet = string.printable + "​\u202E \U0001F600\x00\x1b "
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))


def test_fuzz_both_routes_only_ever_answer_with_fixed_pages(client):
    rng = random.Random(6)
    role(client, "reviewer")
    fields = ("rule_id", "version", "note", "text", "back", "outcome", "author")
    seen = set()
    for i in range(300):
        data = {f: _random_text(rng) for f in fields if rng.random() < 0.7}
        if rng.random() < 0.3:
            data["version"] = rng.choice(["1", "2", "0", "-1", "1.5", "99999999999999999999", "٣"])
        if rng.random() < 0.3:
            data["rule_id"] = rng.choice(["R1", "R2", "R4", "R99", ""])
        sid = rng.choice(["1", "3", "12", "13", "14", "999", "abc", "0"])
        path = rng.choice(["dismiss", "comment"])
        if rng.random() < 0.15:  # some well-formed requests, so the success path is fuzzed too
            sid, path = "3", "comment"
            data = {"text": "fuzz %d %s" % (i, _random_text(rng).replace("\x00", "").replace("\x1b", "")
                                           .replace("\x0b", "").replace("\x0c", "")), "version": "1"}
        r = client.post("/review/%s/%s" % (sid, path), data=data, follow_redirects=False)
        seen.add(r.status_code)
        assert r.status_code in (303, 404, 409, 422), (path, sid, data, r.status_code)
        assert "Traceback" not in r.text and "sqlite" not in r.text.lower() and "/Users/" not in r.text
        assert not r.text.lstrip().startswith("{")
    assert {303, 422} <= seen
    with db.connect() as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ---- caps and abuse ----

def test_comment_cap_returns_the_friendly_409(client):
    role(client, "reviewer")
    with db.connect() as c:
        have = c.execute("SELECT count(*) FROM comment WHERE submission_id = 3").fetchone()[0]
        for i in range(200 - have):
            c.execute("INSERT INTO comment (submission_id, version_number, author, text, created_at)"
                      " VALUES (3, 1, 'x', ?, '2026-10-01T00:00:00Z')", ("c%d" % i,))
    r = comment(client, 3, "one too many")
    assert r.status_code == 409 and "limit of 200 comments" in r.text
    assert comment(client, 12, "other items are fine").status_code == 303


def test_a_hundred_rapid_dismissals_make_one_row(client):
    role(client, "reviewer")
    codes = [dismiss(client, note="attempt %d" % i).status_code for i in range(100)]
    assert codes.count(303) == 1 and codes.count(409) == 99
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM flag_dismissal WHERE rule_id = 'R4' AND version_id ="
                         " (SELECT id FROM version WHERE submission_id = 12)").fetchone()[0] == 1


# ---- headers ----

def test_security_headers_on_every_response_of_both_routes(client):
    cases = []
    for who in ("reviewer", "marketer"):
        role(client, who)
        cases += [dismiss(client), dismiss(client, note=""), dismiss(client, sid=999), dismiss(client, version="9"),
                  comment(client), comment(client, text=""), comment(client, sid=999),
                  client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "x"},
                              headers={"Origin": "https://evil.example"}),
                  client.get("/review/12/dismiss"), client.get("/review/3/comment")]
    assert {r.status_code for r in cases} >= {303, 403, 404, 405, 409, 422}
    for r in cases:
        assert r.headers.get("content-security-policy"), r.status_code
        assert r.headers.get("x-content-type-options") == "nosniff", r.status_code
        if r.request.method == "POST" and r.status_code != 404:  # the shared 404 page sets no cache header
            assert r.headers.get("cache-control") == "no-store", r.status_code


# ---- code-level guards ----

@pytest.mark.parametrize("module", ["review.py", "audit.py", "notes.py", "textclean.py"])
def test_every_sql_statement_is_a_constant_with_bound_values(module):
    tree = ast.parse((APP_DIR / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "execute", "executemany", "executescript"):
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), \
                "%s line %d builds SQL at run time" % (module, node.lineno)


def test_audit_rows_are_written_only_by_the_three_functions():
    inserts = {}
    for path in APP_DIR.rglob("*.py"):
        for table in ("flag_dismissal", "comment", "decision"):
            if re.search(r"INSERT INTO %s\b" % table, path.read_text()):
                inserts.setdefault(table, set()).add(path.name)
    assert inserts == {"flag_dismissal": {"review.py", "seed.py"}, "comment": {"review.py", "seed.py"},
                       "decision": {"review.py", "seed.py"}}


def test_no_unsafe_constructs_in_templates_or_the_script():
    # (submit.html's hx-swap="innerHTML" is an HTMX attribute for the pre-check, not script code.)
    for path in [APP_DIR / "templates" / "review.html", APP_DIR / "templates" / "_flag_cards.html",
                 APP_DIR / "static" / "review.js"]:
        text = path.read_text()
        assert not re.search(r"\|\s*safe|Markup\(|innerHTML|insertAdjacentHTML|document\.write|\beval\(", text), path


def test_the_phase_adds_no_dependency_and_leaks_no_secret_or_path(client):
    requirements = (APP_DIR.parent / "requirements.txt").read_text().lower()
    assert "html" not in requirements and "bleach" not in requirements and "markdown" not in requirements
    role(client, "reviewer")
    pages_ = [dismiss(client), dismiss(client, note=""), comment(client), comment(client, text=""),
              client.get("/review/12"), client.get("/review/14?snippet=R2")]
    for r in pages_:
        body = r.text
        assert "/Users/" not in body and "clearpath.db" not in body and "Traceback" not in body
        assert not re.search(r"(?i)(secret|password|api[_-]?key|token)\s*[:=]", body)
