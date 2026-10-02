import re
import sqlite3

# Review screen logic. Routes only parse input, call these functions and render.

# ASCII digits only, one to nine of them: no sign, spaces, Unicode digits or values that could
# overflow SQLite's integer. fullmatch, because "$" would accept a trailing newline.
_ID = re.compile(r"[0-9]{1,9}")


def parse_id(raw):
    """Return `raw` as an int if it is 1 to 9 ASCII digits, else None. Used for ids and version numbers."""
    if not isinstance(raw, str) or _ID.fullmatch(raw) is None:
        return None
    return int(raw)


def _is_int(value):
    return type(value) is int


def load_review(conn, submission_id, version_number=None):
    """Everything the review screen shows for one version, as plain dicts, or None.

    Returns None when the submission or the requested version does not exist (a version number
    belongs to its own submission only). The default version is the current one. Read-only:
    fixed parameterized queries, nothing is written.
    """
    if not _is_int(submission_id) or (version_number is not None and not _is_int(version_number)):
        return None
    submission = conn.execute(
        "SELECT id, title, product, channel, status, launch_date, submitted_by, created_at,"
        " current_version FROM submission WHERE id = ?", (submission_id,)).fetchone()
    if submission is None:
        return None
    submission = dict(submission)
    number = submission["current_version"] if version_number is None else version_number

    versions = [dict(r) for r in conn.execute(
        "SELECT id, version_number, copy, notes, created_at FROM version"
        " WHERE submission_id = ? ORDER BY version_number", (submission_id,))]
    version = next((v for v in versions if v["version_number"] == number), None)
    if version is None:
        return None

    flags = [dict(r) for r in conn.execute(
        "SELECT id, rule_id, severity, kind, matched_text, start_index, end_index FROM flag"
        " WHERE version_id = ? ORDER BY start_index IS NULL, start_index, rule_id, id",
        (version["id"],))]
    dismissals = [dict(r) for r in conn.execute(
        "SELECT id, rule_id, note, dismissed_by, created_at FROM flag_dismissal"
        " WHERE version_id = ? ORDER BY created_at, id", (version["id"],))]
    decisions = [dict(r) for r in conn.execute(
        "SELECT id, version_number, outcome, reviewer, reason, created_at FROM decision"
        " WHERE submission_id = ? ORDER BY created_at, id", (submission_id,))]
    comments = [dict(r) for r in conn.execute(
        "SELECT id, author, text, rule_id, created_at FROM comment"
        " WHERE submission_id = ? AND version_number = ? ORDER BY created_at, id",
        (submission_id, number))]

    history = [{"kind": "version", "version_number": v["version_number"], "who": submission["submitted_by"],
                "at": v["created_at"]} for v in versions]
    history += [{"kind": "decision", "version_number": d["version_number"], "who": d["reviewer"],
                 "at": d["created_at"], "outcome": d["outcome"], "reason": d["reason"]} for d in decisions]
    history.sort(key=lambda h: (h["at"], h["kind"] != "version"))

    return {
        "submission": submission,
        "version": version,
        "version_numbers": [v["version_number"] for v in versions],
        "version_times": {v["version_number"]: v["created_at"] for v in versions},
        "flags": flags,
        "dismissals": dismissals,
        "decision": next((d for d in decisions if d["version_number"] == number), None),
        "comments": comments,
        "history": history,
    }


def header_view(data, back="/"):
    """Plain-text values for the review page header. The template escapes them."""
    from datetime import date

    from app.queue import _label, _launch_text

    s = data["submission"]
    try:
        launch_text = _launch_text(date.fromisoformat(s["launch_date"]))
    except (ValueError, TypeError):
        launch_text = str(s["launch_date"])
    number = data["version"]["version_number"]
    return {
        "id": s["id"],
        "title": s["title"],
        "product_label": _label("product", s["product"]),
        "channel_label": _label("channel", s["channel"]),
        "status_label": _label("status", s["status"]),
        "launch_text": launch_text,
        "submitted_by": s["submitted_by"],
        "version_text": "v%d" % number,
        "is_current": number == s["current_version"],
        "back_href": back,
        "back_text": "Back to my submissions" if back == MINE_URL else "Back to the queue",
    }


def _span(flag, length):
    """(start, end) of a usable phrase flag, else None. Bad or stale rows are skipped, never raised on."""
    try:
        if flag["kind"] != "phrase":
            return None
        start, end = flag["start_index"], flag["end_index"]
        flag["id"]
    except (KeyError, TypeError, IndexError):
        return None
    if not (_is_int(start) and _is_int(end)) or not 0 <= start < end <= length:
        return None
    return start, end


def segments(copy, flags):
    """Split `copy` into ordered (text, flag_ids) pieces that join back to exactly `copy`.

    A piece covered by no flag has an empty tuple. Overlapping flags split the text so each
    piece lists every flag covering it; adjacent flags stay in separate pieces. Offsets are
    code-point indices into the original copy. Callers pass only the flags to highlight
    (dismissed rules are excluded by the caller). One sweep over sorted cut points.
    """
    spans = []
    for index, flag in enumerate(flags):
        span = _span(flag, len(copy))
        if span is not None:
            spans.append((span[0], span[1], index, flag["id"]))
    if not spans:
        return [(copy, ())] if copy else []

    starts = sorted(spans)
    cuts = sorted({0, len(copy)} | {s[0] for s in spans} | {s[1] for s in spans})
    pieces, active, next_span = [], {}, 0
    for left, right in zip(cuts, cuts[1:]):
        while next_span < len(starts) and starts[next_span][0] <= left:
            start, end, order, flag_id = starts[next_span]
            active[order] = (end, flag_id)
            next_span += 1
        active = {o: v for o, v in active.items() if v[0] > left}
        pieces.append((copy[left:right], tuple(v[1] for _, v in sorted(active.items()))))
    return pieces


_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_WORD = {"high": ("H", "High"), "medium": ("M", "Medium"), "low": ("L", "Low")}


def copy_view(data):
    """Pieces of the copy for the template: text, plus a tag for flagged pieces.

    Dismissed rules are not highlighted. Each flag's anchor id is attached to the first piece it
    appears in only, so ids stay unique when overlaps split a flag. The text stays a plain
    string; the template escapes it.
    """
    dismissed = {d["rule_id"] for d in data["dismissals"]}
    shown = [f for f in data["flags"] if f["rule_id"] not in dismissed]
    by_id = {f["id"]: f for f in shown}
    seen, pieces = set(), []
    for text, ids in segments(data["version"]["copy"], shown):
        if not ids:
            pieces.append({"text": text, "flagged": False})
            continue
        flags = [by_id[i] for i in ids]
        top = min(flags, key=lambda f: _SEVERITY_RANK.get(f["severity"], 3))
        letter, word = _SEVERITY_WORD.get(top["severity"], ("?", "Unknown"))
        new = [i for i in ids if i not in seen]
        seen.update(new)
        pieces.append({
            "text": text, "flagged": True, "severity": top["severity"] if word != "Unknown" else "",
            "letter": letter,
            "label": "%s severity flag: %s" % (word, ", ".join(dict.fromkeys(f["rule_id"] for f in flags))),
            "anchors": new,
        })
    return pieces


MAX_OCCURRENCE_CHARS = 120


def cards_view(data, pieces):
    """Flag cards for the open (not dismissed) rules of the selected version, one card per rule.

    Name, explanation and labels come from rules.describe, the single source of rule text.
    `pieces` is copy_view's output: an occurrence links to its highlight only if that anchor
    exists in the copy, so a link never dangles. Ordered high severity first, then rule id.
    """
    from app.rules import describe

    dismissed = {d["rule_id"] for d in data["dismissals"]}
    anchored = {a for p in pieces for a in p.get("anchors", ())}
    cards = {}
    for f in data["flags"]:
        if f["rule_id"] in dismissed:
            continue
        card = cards.get(f["rule_id"])
        if card is None:
            info = describe(f)
            letter, _ = _SEVERITY_WORD.get(info["severity"], ("?", ""))
            card = cards[f["rule_id"]] = {**info, "letter": letter, "occurrences": []}
        if f["kind"] == "phrase":
            text = f["matched_text"] or ""
            card["occurrences"].append({
                "text": text if len(text) <= MAX_OCCURRENCE_CHARS else text[:MAX_OCCURRENCE_CHARS] + "…",
                "full": text,
                "anchor": f["id"] if f["id"] in anchored else None,
            })
    return sorted(cards.values(), key=lambda c: (_SEVERITY_RANK.get(c["severity"], 3), c["rule_id"]))


def when_text(timestamp):
    """'Oct 2, 2026 14:02 UTC' from a stored UTC timestamp; a malformed value gives a safe fallback."""
    from datetime import datetime

    from app.queue import MONTHS

    try:
        moment = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return "unknown time"
    return "%s %d, %d %02d:%02d UTC" % (MONTHS[moment.month - 1], moment.day, moment.year,
                                        moment.hour, moment.minute)


def dismissals_view(data):
    """Dismissed rules of the selected version: rule name, who, when and the note (plain text)."""
    from app.rules import describe

    out = []
    for d in data["dismissals"]:
        info = describe({"rule_id": d["rule_id"]})
        out.append({"rule_id": info["rule_id"], "name": info["name"], "by": d["dismissed_by"],
                    "when": when_text(d["created_at"]), "note": d["note"]})
    return out


def day_text(timestamp):
    """'Oct 2, 2026' from a stored UTC timestamp, or 'unknown date'."""
    text = when_text(timestamp)
    return "unknown date" if text == "unknown time" else text.rsplit(" ", 2)[0]


def _outcome_label(outcome):
    from app.queue import _label

    return _label("status", outcome)


_LOCK_TEXT = {
    "approved": "Locked: Approved by %s, %s.",
    "changes_requested": "Changes requested by %s, %s. Locked until the marketer resubmits.",
    "rejected": "Locked: Rejected by %s, %s. A new version must be submitted.",
}


def versions_view(data, back="/"):
    """The version selector: one link per version, the viewed one and the current one marked."""
    sid = data["submission"]["id"]
    current = data["submission"]["current_version"]
    shown = data["version"]["version_number"]
    return [{"text": "v%d" % n, "current": n == current, "selected": n == shown,
             "resubmitted": ("Resubmitted " + day_text(data["version_times"].get(n))) if n > 1 else None,
             "href": with_back("/review/%d" % sid if n == current else "/review/%d?v=%d" % (sid, n), back)}
            for n in data["version_numbers"]]


def notices_view(data, back="/"):
    """Banners for the selected version: an old-version notice and a lock banner, either may be None."""
    sid = data["submission"]["id"]
    current = data["submission"]["current_version"]
    shown = data["version"]["version_number"]
    old = None
    if shown != current:
        old = {"text": "You are viewing v%d. The current version is v%d." % (shown, current),
               "href": with_back("/review/%d" % sid, back), "link_text": "Go to v%d" % current}
    lock = None
    d = data["decision"]
    if d is not None:
        template = _LOCK_TEXT.get(d["outcome"], "Decided: %s by %s, %s.")
        who, day = d["reviewer"], day_text(d["created_at"])
        text = template % (who, day) if d["outcome"] in _LOCK_TEXT else "Decided by %s, %s." % (who, day)
        lock = {"text": text, "reason": d["reason"], "outcome": d["outcome"]}
    return {"old": old, "lock": lock}


def feedback_view(data):
    """What the marketer reads at the top of the copy: this version's decision and its comments.

    Plain text for the template to escape. `decision` is None while the version is undecided.
    """
    d = data["decision"]
    decision = None
    if d is not None:
        decision = {"outcome": d["outcome"], "outcome_label": _outcome_label(d["outcome"]),
                    "reviewer": d["reviewer"], "reason": d["reason"], "when": day_text(d["created_at"])}
    return {"version_text": "v%d" % data["version"]["version_number"], "decision": decision,
            "comments": comments_view(data)}


def history_view(data):
    """The history strip: versions submitted and decisions made, oldest first, as plain text."""
    out = []
    for h in data["history"]:
        if h["kind"] == "version":
            text = "v%d submitted by %s" % (h["version_number"], h["who"])
        else:
            text = "v%d %s by %s" % (h["version_number"], _outcome_label(h["outcome"]).lower(), h["who"])
        out.append({"text": text, "when": day_text(h["at"]), "kind": h["kind"],
                    "resubmitted": h["kind"] == "version" and h["version_number"] > 1})
    return out


def _parse_timestamp(value):
    from datetime import datetime

    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def comments_view(data):
    """Read-only comments of the selected version, oldest first, as plain text for the template.

    A comment made after this version's decision is labeled post-decision. If either time is
    unreadable the label is left off rather than guessed. A rule name is shown only for a rule
    that still exists.
    """
    from app.rules import describe

    decided_at = _parse_timestamp(data["decision"]["created_at"]) if data["decision"] else None
    out = []
    for c in data["comments"]:
        posted = _parse_timestamp(c["created_at"])
        rule_name = None
        if c["rule_id"]:
            info = describe({"rule_id": c["rule_id"]})
            rule_name = "%s: %s" % (info["rule_id"], info["name"]) if info["known"] else None
        out.append({
            "author": c["author"], "text": c["text"], "when": when_text(c["created_at"]),
            "rule": rule_name,
            "post_decision": bool(decided_at and posted and posted > decided_at),
        })
    return out


# ---- decisions ----

OUTCOMES = ("approved", "changes_requested", "rejected")
DECIDABLE_STATUSES = ("new", "in_review")
MAX_REASON_CHARS = 2000
# Reasons that must say something: approving needs no reason.
REASON_REQUIRED = ("changes_requested", "rejected")


class DecisionError(Exception):
    """A decision was refused. `code` is one of a fixed set; the message is never user text."""

    CODES = ("bad_outcome", "reason_required", "reason_too_long", "not_found", "stale_version",
             "already_decided", "locked")

    def __init__(self, code):
        assert code in self.CODES
        super().__init__(code)
        self.code = code


def _clean_reason(reason):
    """The reason trimmed, or None if it has no visible characters.

    Beyond the spaces the database CHECK trims, control, format (zero-width) and Unicode space
    characters count as blank, so a reason of only invisible characters is refused.
    """
    import unicodedata

    if reason is None:
        return None
    if not isinstance(reason, str):
        raise TypeError("reason must be text or None")
    if not any(unicodedata.category(ch) not in ("Cc", "Cf", "Zs", "Zl", "Zp") for ch in reason):
        return None
    return reason.strip()


def _insert_decision(conn, submission_id, version_number, outcome, reviewer, reason, created_at):
    conn.execute(
        "INSERT INTO decision (submission_id, version_number, outcome, reviewer, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)", (submission_id, version_number, outcome, reviewer, reason, created_at))


def _update_status(conn, submission_id, status):
    conn.execute("UPDATE submission SET status = ? WHERE id = ?", (status, submission_id))


def record_decision(conn, submission_id, version_number, outcome, reason, reviewer, now):
    """Record a reviewer's decision on the current version of a submission. The only place that does.

    Validates, then takes the write lock (BEGIN IMMEDIATE) before reading, so two requests cannot
    both pass the checks. The decision row and the new status are written in one transaction and
    committed here; any refusal or failure rolls back and leaves the data as it was. Raises
    DecisionError with a fixed code. `version_number` must be the submission's current version;
    anything else, including a version that does not exist, is `stale_version`. A version that
    already has a decision is `already_decided` (checked before status, so a second attempt says
    who decided); `locked` covers a non-decidable status with no decision row to point at.
    """
    from app import seed

    if outcome not in OUTCOMES or not isinstance(outcome, str):
        raise DecisionError("bad_outcome")
    reason = _clean_reason(reason)
    if outcome in REASON_REQUIRED and reason is None:
        raise DecisionError("reason_required")
    if reason is not None and len(reason) > MAX_REASON_CHARS:
        raise DecisionError("reason_too_long")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer must be a non-blank name")
    if not hasattr(now, "tzinfo"):
        raise TypeError("now must be a datetime")
    if conn.in_transaction:
        raise RuntimeError("record_decision needs a connection with no open transaction")

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status, current_version FROM submission WHERE id = ?",
                           (submission_id,)).fetchone() if _is_int(submission_id) else None
        if row is None:
            raise DecisionError("not_found")
        if not _is_int(version_number) or version_number != row["current_version"]:
            raise DecisionError("stale_version")
        if conn.execute("SELECT 1 FROM decision WHERE submission_id = ? AND version_number = ?",
                        (submission_id, version_number)).fetchone():
            raise DecisionError("already_decided")
        if row["status"] not in DECIDABLE_STATUSES:
            raise DecisionError("locked")
        created_at = seed.format_timestamp(now)
        try:
            _insert_decision(conn, submission_id, version_number, outcome, reviewer, reason, created_at)
        except sqlite3.IntegrityError:
            raise DecisionError("already_decided") from None  # the UNIQUE constraint as a backstop
        _update_status(conn, submission_id, outcome)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return {"submission_id": submission_id, "version_number": version_number, "outcome": outcome,
            "reviewer": reviewer, "reason": reason, "created_at": created_at}


# What the page says for each refusal, and the HTTP status that goes with it. Fixed text only.
DECISION_STATUS = {"bad_outcome": 422, "reason_required": 422, "reason_too_long": 422, "not_found": 404,
                   "stale_version": 409, "already_decided": 409, "locked": 409}
DECISION_MESSAGES = {
    "bad_outcome": "Choose Approve, Request changes or Reject.",
    "reason_required": "A reason is required to request changes or reject.",
    "reason_too_long": "Keep the reason under %d characters." % MAX_REASON_CHARS,
    "stale_version": "A newer version exists, so this page was out of date. Your decision was not saved.",
    "locked": "This version is locked. Your decision was not saved.",
    "bad_form": "This form was incomplete or out of date. Reload the page and try again.",
}


def conflict_message(code, data):
    """Banner text for a refused decision, using the page's current state (plain text)."""
    d = data["decision"] if data else None
    if code == "already_decided" and d:
        return "This version was already %s by %s at %s. Your decision was not saved." % (
            _outcome_label(d["outcome"]).lower(), d["reviewer"], when_text(d["created_at"]))
    if code == "already_decided":
        return "This version was already decided. Your decision was not saved."
    return DECISION_MESSAGES.get(code, DECISION_MESSAGES["bad_form"])


def decision_form_view(data, back="/"):
    """What the decision form needs, or None when this page must not offer one.

    A form is offered only for the submission's current version, with no decision on it yet, while
    the status is still new or in review. The role is checked by the template (reviewer sees the
    form, anyone else sees a read-only note). The server enforces all of this again on POST.
    """
    s, v = data["submission"], data["version"]
    if v["version_number"] != s["current_version"] or data["decision"] is not None:
        return None
    if s["status"] not in DECIDABLE_STATUSES:
        return None
    return {"id": s["id"], "version": v["version_number"], "max_reason": MAX_REASON_CHARS,
            "back": back if back != "/" else ""}


# ---- back link to the queue, with its filters ----

class _Query:
    """Just enough of a query-parameter object for queue.filters_from_query."""

    def __init__(self, parsed):
        self._parsed = parsed

    def getlist(self, name):
        return self._parsed.get(name, [])


MINE_URL = "/mine"


def safe_back(raw):
    """The URL to go back to: "/", "/mine" or "/?status=..&product=..&channel=..", always rebuilt here.

    "/mine" is allowed only as exactly that text; it is how a marketer returns to their list.

    `raw` is untrusted. It must start with "/?"; its query is parsed and only the three known
    filters with allowed values are kept (a repeated or unknown value is dropped), and a fresh
    URL is built from those. Nothing from `raw` is passed through, so it cannot point off-site,
    carry markup or add parameters. Anything else gives "/".
    """
    from urllib.parse import parse_qs, urlencode

    from app.queue import FILTER_FIELDS, filters_from_query

    if raw == MINE_URL:
        return MINE_URL
    if not isinstance(raw, str) or len(raw) > 300 or not raw.startswith("/?"):
        return "/"
    try:
        parsed = parse_qs(raw[2:], keep_blank_values=True, max_num_fields=20)
    except ValueError:
        return "/"
    filters = filters_from_query(_Query(parsed))
    pairs = [(name, filters[name]) for name, _ in FILTER_FIELDS if filters[name]]
    return "/?" + urlencode(pairs) if pairs else "/"


def with_back(url, back):
    """`url` with ?back=<back> (or &back=) added, unless back is the plain queue."""
    from urllib.parse import urlencode

    if back == "/":
        return url
    return url + ("&" if "?" in url else "?") + urlencode({"back": back})
