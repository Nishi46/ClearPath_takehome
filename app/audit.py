from datetime import datetime

# The audit trail of one submission: the ordered union of its versions, flag dismissals, comments
# and decisions, each with who, what and when. Read-only: nothing here writes, and the clock is
# passed in. Presentation is plain strings; escaping is the template's job.

# Events with the same timestamp are ordered by version number (so v1's decision comes before v2's
# submission even within one second), then by kind (this order), then by id.
KIND_ORDER = ("version", "dismissal", "comment", "decision")
KIND_WORDS = {"version": "Version", "dismissal": "Dismissed", "comment": "Comment", "decision": "Decision"}

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(value):
    try:
        return datetime.strptime(value, _TIME_FORMAT)
    except (ValueError, TypeError):
        return None


def _sort_key(event):
    moment = _parse(event["at"])
    # An unreadable timestamp sorts last and keeps its row: never dropped, never guessed.
    return (moment is None, moment or datetime.min, event["version_number"],
            KIND_ORDER.index(event["kind"]), event["id"])


def load_trail(conn, submission_id):
    """All events of a submission, oldest first, or None if it does not exist.

    Four fixed parameterized queries (a constant number, however long the trail is), run in one
    read transaction so they see the same state. Each event is a dict with `kind`, `at`, `who`,
    `version_number`, `id` and the kind's own fields: `rule_id` and `note` (dismissal), `text` and
    `rule_id` (comment), `outcome` and `reason` (decision).
    """
    if type(submission_id) is not int:
        return None
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        versions = conn.execute(
            "SELECT v.id, v.version_number, v.created_at, s.submitted_by FROM version v"
            " JOIN submission s ON s.id = v.submission_id WHERE v.submission_id = ?",
            (submission_id,)).fetchall()
        if not versions:
            return None
        dismissals = conn.execute(
            "SELECT d.id, d.rule_id, d.note, d.dismissed_by, d.created_at, v.version_number"
            " FROM flag_dismissal d JOIN version v ON v.id = d.version_id WHERE v.submission_id = ?",
            (submission_id,)).fetchall()
        comments = conn.execute(
            "SELECT id, version_number, author, text, rule_id, created_at FROM comment"
            " WHERE submission_id = ?", (submission_id,)).fetchall()
        decisions = conn.execute(
            "SELECT id, version_number, reviewer, outcome, reason, created_at FROM decision"
            " WHERE submission_id = ?", (submission_id,)).fetchall()
    finally:
        if started:
            conn.rollback()  # a read transaction: nothing to keep

    events = [{"kind": "version", "id": v["id"], "at": v["created_at"], "who": v["submitted_by"],
               "version_number": v["version_number"]} for v in versions]
    events += [{"kind": "dismissal", "id": d["id"], "at": d["created_at"], "who": d["dismissed_by"],
                "version_number": d["version_number"], "rule_id": d["rule_id"], "note": d["note"]}
               for d in dismissals]
    events += [{"kind": "comment", "id": c["id"], "at": c["created_at"], "who": c["author"],
                "version_number": c["version_number"], "text": c["text"], "rule_id": c["rule_id"]}
               for c in comments]
    events += [{"kind": "decision", "id": d["id"], "at": d["created_at"], "who": d["reviewer"],
                "version_number": d["version_number"], "outcome": d["outcome"], "reason": d["reason"]}
               for d in decisions]
    events.sort(key=_sort_key)
    return events


def relative_time(at, now):
    """'just now', '5 minutes ago', '3 hours ago', '2 days ago'; 'unknown time' if unreadable.

    A time in the future (clock skew) reads 'just now', never a negative amount.
    """
    moment = _parse(at)
    if moment is None:
        return "unknown time"
    seconds = int((now.replace(tzinfo=None) - moment).total_seconds())
    if seconds < 60:
        return "just now"
    for size, word in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return "%d %s%s ago" % (n, word, "" if n == 1 else "s")


def _rule_text(rule_id):
    """'R4: Rule name', or just the id for a rule that no longer exists."""
    from app.rules import describe

    info = describe({"rule_id": rule_id})
    return "%s: %s" % (rule_id, info["name"]) if info["known"] else str(rule_id)


def trail_view(events, now):
    """One dict of plain strings per event for the template, in the order given.

    `when` is relative, `absolute` is the readable UTC time, `exact` is the stored timestamp (for
    <time datetime>, or None if unreadable). `detail` is the full note, comment or reason text,
    never truncated. Decision labels use the same words as the history strip.
    """
    from app.review import _outcome_label, when_text

    out = []
    for e in events:
        kind = e["kind"]
        if kind == "version":
            label, detail = "v%d submitted" % e["version_number"], ""
        elif kind == "dismissal":
            label, detail = "Flag dismissed: " + _rule_text(e["rule_id"]), e["note"]
        elif kind == "comment":
            label = "Comment on v%d" % e["version_number"]
            if e["rule_id"]:
                label += " (%s)" % _rule_text(e["rule_id"])
            detail = e["text"]
        else:
            label, detail = _outcome_label(e["outcome"]), e["reason"] or ""
        out.append({"kind": kind, "kind_word": KIND_WORDS[kind], "who": e["who"], "label": label,
                    "detail": detail, "version": "v%d" % e["version_number"],
                    "when": relative_time(e["at"], now), "absolute": when_text(e["at"]),
                    "exact": e["at"] if _parse(e["at"]) else None})
    return out
