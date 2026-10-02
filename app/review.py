import re

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
        "flags": flags,
        "dismissals": dismissals,
        "decision": next((d for d in decisions if d["version_number"] == number), None),
        "comments": comments,
        "history": history,
    }


def header_view(data):
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
