"""Collect every user-visible error, empty state, warning, notice and button label by rendering the app.

    .venv/bin/python -m tests.tools.collect_copy              # print the inventory
    .venv/bin/python -m tests.tools.collect_copy --write      # rewrite documentation/copy-inventory.md

It starts the app on a temporary database with the clock pinned to a Wednesday, visits each screen as
each role (including the refusals a script or a stale tab would hit), and pulls out the texts that
tell a person what happened or what to do next. Digits are replaced with N so counts and dates
do not make the inventory change from day to day.
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

INVENTORY = Path(__file__).resolve().parents[2] / "documentation" / "copy-inventory.md"
ORIGIN = {"origin": "http://testserver"}

# An element is "copy" when its role or any class says so.
ROLES = {"alert", "status"}
CLASS_HINTS = ("notice", "empty", "error", "warning", "banner", "readonly", "table-note", "note", "lock", "hint")
IGNORE_CLASSES = {"visually-hidden", "reset-demo-note", "notes-text"}   # the reset note is chrome, listed once below


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []          # (kind, text)
        self._stack = []         # open copy elements: [kind, tag, depth, parts]
        self._button = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if self._stack and tag == self._stack[-1][1]:
            self._stack[-1][2] += 1
        elif not self._stack and tag in ("p", "div", "span", "li", "small", "section", "h1", "label", "td"):
            kind = None
            if a.get("role") in ROLES:
                kind = a["role"]
            elif not IGNORE_CLASSES & set(classes):
                # "has-*" are state modifiers on big containers; "notes-text" is the submitter's own words.
                hits = [c for c in classes if not c.startswith("has-") and any(h in c for h in CLASS_HINTS)]
                kind = hits[0] if hits else None
            if kind:
                self._stack.append([kind, tag, 1, []])
        if tag == "button" or (tag == "a" and any("button" in c for c in classes)):
            self._button = ["button", tag, 1, []]
        if tag == "input" and a.get("type") == "submit" and a.get("value"):
            self.found.append(("button", a["value"]))
        if tag == "title":
            self._button = ["title", tag, 1, []]

    def handle_data(self, data):
        if self._stack:
            self._stack[-1][3].append(data)
        if self._button:
            self._button[3].append(data)

    def handle_endtag(self, tag):
        if self._button and tag == self._button[1]:
            self._emit(self._button)
            self._button = None
        if self._stack and tag == self._stack[-1][1]:
            self._stack[-1][2] -= 1
            if self._stack[-1][2] == 0:
                self._emit(self._stack.pop())

    def _emit(self, item):
        text = re.sub(r"\s+", " ", "".join(item[3])).strip()
        if text and item[0] != "title":
            self.found.append((item[0], text))


def normalize(text):
    # Buttons that carry a submission title ("View Spring promo") are listed once, with a placeholder.
    text = re.sub(r"^(View|Edit and resubmit|Fix and resubmit as new version) .+$", r"\1 <title>", text)
    text = re.sub(r"\d+(,\d{3})*", "N", text)
    return re.sub("(Submitted\\.|Resubmitted as vN\\.) \u201c.*?\u201d", lambda m: m.group(1) + " \u201c<title>\u201d", text)


def extract(html):
    c = Collector()
    c.feed(html)
    return {(kind, normalize(text)) for kind, text in c.found}


def _client_factory():
    os.environ["CLEARPATH_DB"] = os.path.join(tempfile.mkdtemp(), "copy.db")
    from fastapi.testclient import TestClient

    from app import clock
    from app.main import app

    moment = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)   # a Wednesday
    clock.now = lambda: moment
    return TestClient(app, raise_server_exceptions=False)


def _as(client, role, marketer="Maya Chen"):
    client.cookies.set("role", role)
    client.cookies.set("marketer", marketer)
    return client


def collect():
    """{(kind, text): sorted list of screens it appears on}."""
    from app.routes import pages
    from tests.test_resubmit_route import stored_copy  # noqa: F401  (imported lazily: needs the app)

    found = {}

    def see(screen, response):
        for item in extract(response.text):
            found.setdefault(item, set()).add(screen)

    with _client_factory() as c:
        form = {"title": "Spring promo", "product": "loan", "channel": "email", "notes": "",
                "copy": "Apply today. Rates from 5.99% APR. Subject to credit approval.",
                "launch_date": "2026-11-20"}
        for role in ("reviewer", "marketer"):
            _as(c, role)
            for path in ["/", "/?status=rejected&product=card", "/submit", "/mine", "/mine?submitted=1",
                         "/reset/confirm", "/nowhere", "/review/abc"] + ["/review/%d" % i for i in range(1, 15)] + [
                         "/review/5?diff=1", "/review/14?snippet=R2", "/review/3?v=9", "/resubmit/14", "/resubmit/6"]:
                see("%s %s" % (role, path.split("?")[0] if not path.startswith("/review/") else "/review/N"
                               if "?" not in path else path), c.get(path))
            see(role + " GET /role", c.get("/role"))                 # wrong method
            see(role + " POST /role bad", c.post("/role", data={"role": "x"}))
        # marketers with other histories
        for name in ("Jordan Lee", "Sam Patel"):
            _as(c, "marketer", name)
            see("marketer %s /mine" % name, c.get("/mine"))
            see("marketer %s /resubmit/14" % name, c.get("/resubmit/14"))
            see("marketer %s /resubmit/8" % name, c.get("/resubmit/8"))
        # refusals and errors a person can reach
        _as(c, "marketer")
        see("submit errors", c.post("/submit", data={**form, "title": "", "copy": " ", "launch_date": "x"}, headers=ORIGIN))
        see("submit past", c.post("/submit", data={**form, "launch_date": "2026-10-01", "copy": ""}, headers=ORIGIN))
        see("submit rush", c.post("/submit", data={**form, "launch_date": "2026-10-08", "copy": ""}, headers=ORIGIN))
        see("submit check", c.post("/submit/check", data=form, headers=ORIGIN))
        see("submit check clean", c.post("/submit/check", data={**form, "product": "mortgage", "channel": "email",
            "copy": _clean_copy(c)}, headers=ORIGIN))
        see("submit check empty", c.post("/submit/check", data={"product": "", "channel": "", "copy": ""}, headers=ORIGIN))
        see("submit cross-origin", c.post("/submit", data=form, headers={"origin": "https://evil.example"}))
        see("submit ok -> mine", c.post("/submit", data=form, headers=ORIGIN, follow_redirects=True))
        see("submit duplicate", c.post("/submit", data=form, headers=ORIGIN))
        see("marketer POST /marketer bad", c.post("/marketer", data={"name": "x"}, headers=ORIGIN))
        _as(c, "reviewer")
        see("reviewer POST /submit", c.post("/submit", data=form, headers=ORIGIN))
        see("reviewer POST /resubmit", c.post("/resubmit/14", data={}, headers=ORIGIN))
        _as(c, "marketer", "Jordan Lee")
        same = {"copy": _copy_of(c, 14), "notes": "", "base_version": "1", "launch_date": "2026-11-20"}
        see("resubmit unchanged", c.post("/resubmit/14", data=same, headers=ORIGIN))
        see("resubmit bad", c.post("/resubmit/14", data={**same, "copy": " ", "launch_date": "x"}, headers=ORIGIN))
        see("resubmit stale", c.post("/resubmit/14", data={**same, "copy": "New words. 5.99% APR.", "base_version": "7"},
                                      headers=ORIGIN))
        see("resubmit locked", c.post("/resubmit/6", data=same, headers=ORIGIN))
        see("resubmit check", c.post("/resubmit/14", data={**same, "copy": "New words. 5.99% APR.", "action": "check"},
                                      headers=ORIGIN))
        _as(c, "marketer", "Maya Chen")
        see("resubmit not owner", c.post("/resubmit/14", data=same, headers=ORIGIN))
        see("marketer POST decision", c.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=ORIGIN))
        see("marketer POST dismiss", c.post("/review/12/dismiss", data={}, headers=ORIGIN))
        see("marketer POST comment", c.post("/review/3/comment", data={}, headers=ORIGIN))
        _as(c, "reviewer")
        for outcome, reason in (("rejected", ""), ("changes_requested", "x" * 2001), ("bogus", "r")):
            see("decision %s" % outcome, c.post("/review/3/decision", data={"outcome": outcome, "version": "1",
                "reason": reason}, headers=ORIGIN))
        see("decision stale", c.post("/review/5/decision", data={"outcome": "approved", "version": "1"}, headers=ORIGIN))
        see("decision locked", c.post("/review/6/decision", data={"outcome": "approved", "version": "1"}, headers=ORIGIN))
        see("decision cross-origin", c.post("/review/3/decision", data={}, headers={"origin": "https://evil.example"}))
        see("decision bad id", c.post("/review/abc/decision", data={}, headers=ORIGIN))
        see("dismiss no note", c.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": " "}, headers=ORIGIN))
        see("dismiss unfired", c.post("/review/10/dismiss", data={"rule_id": "R4", "version": "1", "note": "n"}, headers=ORIGIN))
        see("dismiss locked", c.post("/review/6/dismiss", data={"rule_id": "R4", "version": "1", "note": "n"}, headers=ORIGIN))
        see("dismiss ok", c.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "Quoted use."},
                                 headers=ORIGIN, follow_redirects=True))
        see("dismiss again", c.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "Quoted use."}, headers=ORIGIN))
        see("comment empty", c.post("/review/3/comment", data={"text": " ", "version": "1"}, headers=ORIGIN))
        see("comment old version", c.post("/review/5/comment", data={"text": "hi", "version": "1"}, headers=ORIGIN))
        see("comment ok", c.post("/review/3/comment", data={"text": "Looks fine.", "version": "1"}, headers=ORIGIN,
                                 follow_redirects=True))
        see("comment repeat", c.post("/review/3/comment", data={"text": "Looks fine.", "version": "1"}, headers=ORIGIN))
        see("decision ok", c.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=ORIGIN,
                                  follow_redirects=True))
        see("decision again", c.post("/review/3/decision", data={"outcome": "approved", "version": "1"}, headers=ORIGIN))
        see("reset confirm missing", c.post("/reset", data={}, headers=ORIGIN))
        see("reset cross-origin", c.post("/reset", data={"confirm": "reset"}, headers={"origin": "https://evil.example"}))
        pages.reset_cooldown.clear()
        see("reset ok", c.post("/reset", data={"confirm": "reset"}, headers=ORIGIN, follow_redirects=True))
        see("reset cooldown", c.post("/reset", data={"confirm": "reset"}, headers=ORIGIN))
        see("too large", c.post("/submit", content=b"x=" + b"a" * (1024 * 1024 + 10), headers={
            **ORIGIN, "content-type": "application/x-www-form-urlencoded"}))
        # an empty queue
        from app import db
        pages.reset_cooldown.clear()
        with db.connect() as conn:
            conn.execute("DELETE FROM submission")
        for role in ("reviewer", "marketer"):
            _as(c, role)
            see("%s / (empty queue)" % role, c.get("/"))
            see("%s /mine (empty queue)" % role, c.get("/mine"))
    return {k: sorted(v) for k, v in found.items()}


def _copy_of(client, sid):
    from app import db

    with db.connect() as c:
        return c.execute("SELECT copy FROM version WHERE submission_id = ? AND version_number = 1", (sid,)).fetchone()[0]


def _clean_copy(client):
    return _copy_of(client, 6)


def render_inventory(found):
    lines = ["# Copy inventory", "",
             "> Generated by `.venv/bin/python -m tests.tools.collect_copy --write`. Every error, empty state, warning, "
             "notice and button label the app can show, as rendered. Numbers show as N. Do not edit by hand: change the "
             "template or `app/errors.py`, then regenerate. `tests/test_phase7_copy.py` fails if this file is stale.", "",
             "Patterns (see phase-7-steps.md, decision 3): an error says what is wrong and how to fix it; an empty state says "
             "what is missing and offers a next action; a warning says the risk and what happens next. Plain, specific, "
             "sentence case, no blame, no exclamation marks.", ""]
    by_kind = {}
    for (kind, text), screens in sorted(found.items()):
        by_kind.setdefault("button" if kind == "button" else "message", []).append((kind, text, screens))
    for title, key in (("Messages", "message"), ("Buttons and button-links", "button")):
        lines += ["## " + title, "", "| Kind | Text | First seen on |", "|---|---|---|"]
        for kind, text, screens in by_kind.get(key, []):
            first = sorted(screens, key=lambda name: (not name.startswith(("reviewer", "marketer")), name))[0]
            lines.append("| %s | %s | %s |" % (kind, text.replace("|", "\\|"), first))
        lines.append("")
    return "\n".join(lines)


def main(argv):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    text = render_inventory(collect())
    if "--write" in argv:
        INVENTORY.write_text(text)
        print("wrote", INVENTORY)
    else:
        print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
