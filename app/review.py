import re
import sqlite3

from app.notes import (COMMENT_MESSAGES, MAX_COMMENT_CHARS, MAX_NOTE_CHARS, MESSAGES as NOTE_MESSAGES,
                       validate_comment, validate_note)
from app.textclean import clean_text

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

    # The version just before the one shown, for the diff view: its copy and its open rules.
    previous = None
    before = next((v for v in versions if v["version_number"] == number - 1), None)
    if before is not None:
        gone = {r[0] for r in conn.execute("SELECT rule_id FROM flag_dismissal WHERE version_id = ?", (before["id"],))}
        rules_before = {r[0] for r in conn.execute("SELECT rule_id FROM flag WHERE version_id = ?", (before["id"],))}
        previous = {"version_number": before["version_number"], "copy": before["copy"],
                    "open_rules": rules_before - gone}

    history = [{"kind": "version", "version_number": v["version_number"], "who": submission["submitted_by"],
                "at": v["created_at"]} for v in versions]
    history += [{"kind": "decision", "version_number": d["version_number"], "who": d["reviewer"],
                 "at": d["created_at"], "outcome": d["outcome"], "reason": d["reason"]} for d in decisions]
    history.sort(key=lambda h: (h["at"], h["kind"] != "version"))

    return {
        "submission": submission,
        "version": version,
        "version_numbers": [v["version_number"] for v in versions],
        "previous": previous,
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
                    "when": when_text(d["created_at"]), "note": d["note"], "snippet": info["snippet"]})
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


def review_href(sid, number, current, back="/", diff=False):
    """Link to a review page: the current version has no ?v, and ?diff=1 is kept when asked."""
    url = "/review/%d" % sid if number == current else "/review/%d?v=%d" % (sid, number)
    if diff:
        url += ("&" if "?" in url else "?") + "diff=1"
    return with_back(url, back)


def versions_view(data, back="/", diff=False):
    """The version selector: one link per version, the viewed one and the current one marked."""
    sid = data["submission"]["id"]
    current = data["submission"]["current_version"]
    shown = data["version"]["version_number"]
    return [{"text": "v%d" % n, "current": n == current, "selected": n == shown,
             "resubmitted": ("Resubmitted " + day_text(data["version_times"].get(n))) if n > 1 else None,
             "href": review_href(sid, n, current, back, diff and n > 1)}
            for n in data["version_numbers"]]


def diff_view(data, wanted, back="/"):
    """The "changes since the previous version" view of the shown version, as plain data.

    `wanted` is whether ?diff=1 was given. Returns None when it is not wanted and there is
    nothing to offer (v1). Otherwise a dict: `toggle_text` and `toggle_href` (absent on v1), `on`,
    and when on either `first` (v1: nothing to compare), `too_long`, or `pieces` (list of
    {"kind", "text"}), `summary` and `flags_line`. Everything is text; the template escapes it.
    """
    from app import diff

    sid = data["submission"]["id"]
    current = data["submission"]["current_version"]
    number = data["version"]["version_number"]
    previous = data["previous"]
    if number == 1 or previous is None:
        return {"on": True, "first": True} if wanted else None
    out = {"on": wanted,
           "toggle_text": "Show copy" if wanted else "Changes since v%d" % previous["version_number"],
           "toggle_href": review_href(sid, number, current, back, not wanted)}
    if not wanted:
        return out
    pieces = diff.diff_text(previous["copy"], data["version"]["copy"])
    if pieces is None:
        out["too_long"] = True
        return out
    added, removed = diff.count_words(pieces, "added"), diff.count_words(pieces, "removed")
    out["pieces"] = [{"kind": k, "text": t} for k, t in pieces]
    out["summary"] = "%d word%s added, %d removed" % (added, "" if added == 1 else "s", removed)
    now = {f["rule_id"] for f in data["flags"]} - {d["rule_id"] for d in data["dismissals"]}
    fixed, new = sorted(previous["open_rules"] - now), sorted(now - previous["open_rules"])
    out["flags_line"] = "Flags: fixed %s. New: %s" % (", ".join(fixed) or "none", ", ".join(new) or "none")
    return out


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


# ---- comments ----

MAX_COMMENTS_PER_SUBMISSION = 200


class CommentError(Exception):
    """A comment was refused. `code` is one of a fixed set; the message is never user text."""

    CODES = ("not_found", "stale_version", "text_required", "text_too_long", "text_bad_chars",
             "no_such_flag", "capacity", "duplicate")

    def __init__(self, code):
        assert code in self.CODES
        super().__init__(code)
        self.code = code


def add_comment(conn, submission_id, version_number, text, rule_id, author, now):
    """Record a reviewer comment on the current version of a submission. The only place that does.

    Allowed on any status, including approved and rejected: a comment never changes the status
    and is not a decision. The text is validated first; then the write lock is taken (BEGIN
    IMMEDIATE) and, in order: the submission exists; `version_number` is its current version
    (`stale_version`); `rule_id`, if not None, matches a flag row on that version (dismissed or
    open, but a rule that merely exists in rules.json is not enough: `no_such_flag`, and "" is
    not None); the submission is under the comment cap (`capacity`); the text is not identical to
    this author's latest comment on the version (`duplicate`, which covers a double click).
    The author and timestamp come from the caller, never from the browser. Commits once and
    returns the new row; any refusal or failure rolls back and raises CommentError.
    """
    from app import seed

    clean, code = validate_comment(text)
    if code:
        raise CommentError(code)
    if not isinstance(author, str) or not author.strip():
        raise ValueError("author must be a non-blank name")
    if not hasattr(now, "tzinfo"):
        raise TypeError("now must be a datetime")
    if conn.in_transaction:
        raise RuntimeError("add_comment needs a connection with no open transaction")

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT current_version FROM submission WHERE id = ?",
                           (submission_id,)).fetchone() if _is_int(submission_id) else None
        if row is None:
            raise CommentError("not_found")
        if not _is_int(version_number) or version_number != row["current_version"]:
            raise CommentError("stale_version")
        if rule_id is not None:
            version_id = conn.execute("SELECT id FROM version WHERE submission_id = ? AND version_number = ?",
                                      (submission_id, version_number)).fetchone()["id"]
            if not isinstance(rule_id, str) or not conn.execute(
                    "SELECT 1 FROM flag WHERE version_id = ? AND rule_id = ?", (version_id, rule_id)).fetchone():
                raise CommentError("no_such_flag")
        if conn.execute("SELECT count(*) FROM comment WHERE submission_id = ?",
                        (submission_id,)).fetchone()[0] >= MAX_COMMENTS_PER_SUBMISSION:
            raise CommentError("capacity")
        last = conn.execute("SELECT author, text, rule_id FROM comment WHERE submission_id = ?"
                            " AND version_number = ? AND author = ? ORDER BY id DESC LIMIT 1",
                            (submission_id, version_number, author)).fetchone()
        if last is not None and last["text"] == clean and last["rule_id"] == rule_id:
            raise CommentError("duplicate")
        created_at = seed.format_timestamp(now)
        conn.execute(
            "INSERT INTO comment (submission_id, version_number, author, text, rule_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)", (submission_id, version_number, author, clean, rule_id, created_at))
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return {"submission_id": submission_id, "version_number": version_number, "author": author,
            "text": clean, "rule_id": rule_id, "created_at": created_at}


COMMENT_FIELD_CODES = ("text_required", "text_too_long", "text_bad_chars")
COMMENT_STATUS = {"text_required": 422, "text_too_long": 422, "text_bad_chars": 422, "no_such_flag": 422,
                  "not_found": 404, "stale_version": 409, "capacity": 409, "duplicate": 409}
COMMENT_CONFLICTS = {
    "stale_version": "A newer version exists, so this page was out of date. Your comment was not saved.",
    "duplicate": "You just posted this comment, so it was not posted again.",
    "capacity": "This item has reached the limit of %d comments. Your comment was not saved." % MAX_COMMENTS_PER_SUBMISSION,
}


def comment_form_view(data, back="/"):
    """What the comment form needs, or None when this page must not offer one.

    Offered only on the submission's current version, in any status. The template adds the role
    check; the server enforces both again on POST.
    """
    s, v = data["submission"], data["version"]
    if v["version_number"] != s["current_version"]:
        return None
    return {"id": s["id"], "version": v["version_number"], "max_text": MAX_COMMENT_CHARS,
            "back": back if back != "/" else ""}


def snippet_href(sid, number, current, rule_id, back="/"):
    """Link that reloads the review page with `rule_id`'s snippet prefilled in the comment box."""
    from urllib.parse import urlencode

    query = ([("v", number)] if number != current else []) + [("snippet", rule_id)]
    if back != "/":
        query.append(("back", back))
    return "/review/%d?%s#comment-form" % (sid, urlencode(query))


def prefill_view(data, wanted, back="/"):
    """The comment box state for ?snippet=, or None to leave the box empty.

    `wanted` is the list of `snippet` query values. It counts only when exactly one value is given
    and that rule has a flag on the selected version and has snippet text. The text comes from
    rules.json through rules.describe: the query value is only a lookup key and is never printed.
    The caller checks the role and that the comment form is offered.
    """
    from app.rules import describe

    if len(wanted) != 1 or not isinstance(wanted[0], str):
        return None
    for f in data["flags"]:
        if f["rule_id"] == wanted[0]:
            info = describe(f)
            if info["snippet"]:
                return {"text": info["snippet"], "rule_id": f["rule_id"], "snippet": True, "error": None}
    return None


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
    reason = clean_text(reason)
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


# ---- flag dismissals ----

class DismissError(Exception):
    """A dismissal was refused. `code` is one of a fixed set; the message is never user text."""

    CODES = ("not_found", "stale_version", "already_decided", "locked", "no_such_flag",
             "already_dismissed", "note_required", "note_too_long", "note_bad_chars")

    def __init__(self, code):
        assert code in self.CODES
        super().__init__(code)
        self.code = code


def dismiss_flag(conn, submission_id, version_number, rule_id, note, reviewer, now):
    """Record a reviewer's dismissal of one rule's flag on the current version. The only place that does.

    The note is validated first (no database needed). Then the write lock is taken (BEGIN
    IMMEDIATE) before anything is read, so two requests cannot both pass the checks. In order:
    the submission exists; `version_number` is its current version (`stale_version`); that
    version has no decision (`already_decided`); the status is still new or in review
    (`locked`, the fallback for a status with no decision row); a flag row for `rule_id` exists
    on that version (`no_such_flag`: a rule that merely exists in rules.json is not enough);
    it is not already dismissed (`already_dismissed`). The reviewer and timestamp come from the
    caller, never from the browser. A dismissal is permanent: nothing edits or deletes it.
    Commits once and returns the new row; any refusal or failure rolls back and raises
    DismissError with a fixed code.
    """
    from app import seed

    clean, code = validate_note(note)
    if code:
        raise DismissError(code)
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer must be a non-blank name")
    if not hasattr(now, "tzinfo"):
        raise TypeError("now must be a datetime")
    if conn.in_transaction:
        raise RuntimeError("dismiss_flag needs a connection with no open transaction")

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status, current_version FROM submission WHERE id = ?",
                           (submission_id,)).fetchone() if _is_int(submission_id) else None
        if row is None:
            raise DismissError("not_found")
        if not _is_int(version_number) or version_number != row["current_version"]:
            raise DismissError("stale_version")
        if conn.execute("SELECT 1 FROM decision WHERE submission_id = ? AND version_number = ?",
                        (submission_id, version_number)).fetchone():
            raise DismissError("already_decided")
        if row["status"] not in DECIDABLE_STATUSES:
            raise DismissError("locked")
        version_id = conn.execute("SELECT id FROM version WHERE submission_id = ? AND version_number = ?",
                                  (submission_id, version_number)).fetchone()["id"]
        if not isinstance(rule_id, str) or not conn.execute(
                "SELECT 1 FROM flag WHERE version_id = ? AND rule_id = ?", (version_id, rule_id)).fetchone():
            raise DismissError("no_such_flag")
        created_at = seed.format_timestamp(now)
        try:
            conn.execute(
                "INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                " VALUES (?, ?, ?, ?, ?)", (version_id, rule_id, clean, reviewer, created_at))
        except sqlite3.IntegrityError:
            raise DismissError("already_dismissed") from None  # the UNIQUE constraint as a backstop
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return {"submission_id": submission_id, "version_number": version_number, "rule_id": rule_id,
            "note": clean, "dismissed_by": reviewer, "created_at": created_at}


NOTE_CODES = ("note_required", "note_too_long", "note_bad_chars")
DISMISS_STATUS = {"note_required": 422, "note_too_long": 422, "note_bad_chars": 422, "no_such_flag": 422,
                  "not_found": 404, "stale_version": 409, "already_decided": 409, "locked": 409,
                  "already_dismissed": 409}


def dismiss_conflict_message(code, data, rule_id):
    """Banner text for a refused dismissal, from the page's current state (plain text)."""
    if code == "already_dismissed":
        for d in data["dismissals"]:
            if d["rule_id"] == rule_id:
                return "%s was already dismissed by %s at %s. Your dismissal was not saved." % (
                    d["rule_id"], d["dismissed_by"], when_text(d["created_at"]))
        return "This flag was already dismissed. Your dismissal was not saved."
    if code == "already_decided":
        d = data["decision"]
        if d:
            return "This version was already %s by %s at %s. Your dismissal was not saved." % (
                _outcome_label(d["outcome"]).lower(), d["reviewer"], when_text(d["created_at"]))
        return "This version was already decided. Your dismissal was not saved."
    if code == "stale_version":
        return "A newer version exists, so this page was out of date. Your dismissal was not saved."
    if code == "locked":
        return "This version is locked. Your dismissal was not saved."
    return DECISION_MESSAGES["bad_form"]


def dismiss_form_view(data, back="/"):
    """What the dismiss forms need, or None when this page must not offer any.

    Offered under the same conditions as a decision: the current version, no decision yet, status
    new or in review. The template adds the role check; the server enforces all of it on POST.
    """
    base = decision_form_view(data, back)
    return None if base is None else {**base, "max_note": MAX_NOTE_CHARS}


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
