import re
from datetime import date, timedelta

from app import clock, flags
from app.choices import CHANNELS, PRODUCTS
from app.roles import ALL_SUBMITTERS, SUBMITTING_ROLES

# Submitting a piece of copy for review. Pure checks first (normalize and validate), then the
# read-only pre-check, then create_submission, the only code that creates a submission. Every
# statement binds its values as parameters; nothing from a form is ever part of SQL text.

MAX_TITLE_CHARS = 120
MAX_COPY_CHARS = 10_000
MAX_NOTES_CHARS = 2_000
MAX_VERSIONS = 10  # per submission, for the same reason
MAX_SUBMISSIONS = 300  # the demo database is shared and public: a script must not be able to fill it
MAX_YEARS_AHEAD_DAYS = 2 * 365
MAX_DAYS_BACK = 365

MESSAGES = {
    "title_required": "Add a title.",
    "title_long": "Keep the title to %d characters or fewer." % MAX_TITLE_CHARS,
    "title_lines": "The title must be a single line.",
    "title_chars": "The title contains characters that aren't allowed.",
    "product": "Choose a product.",
    "channel": "Choose a channel.",
    "launch_required": "Choose a launch date.",
    "launch_format": "Enter a real launch date as YYYY-MM-DD.",
    "launch_far": "That launch date is more than 2 years away. Check the year.",
    "launch_old": "That launch date is more than a year ago. Check the year.",
    "copy_required": "Copy can't be empty or only spaces.",
    "copy_long": "Keep the copy to {:,} characters or fewer.".format(MAX_COPY_CHARS),
    "copy_chars": "The copy contains characters that aren't allowed.",
    "notes_long": "Keep the notes to {:,} characters or fewer.".format(MAX_NOTES_CHARS),
    "notes_chars": "The notes contain characters that aren't allowed.",
}

# Spaces and the invisible characters the rules engine also ignores. A value made only of these is blank.
_BLANK = re.compile(r"[\s​-‍⁠﻿]*")
# Control characters except tab and newline (carriage returns are turned into newlines first).
_CONTROL = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f\\u202a-\\u202e\\u2066-\\u2069]")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def normalize_copy(raw):
    """The copy as it is stored, checked, flagged and diffed: CRLF and CR become LF, ends trimmed.

    Browsers send CRLF from a textarea; storing it would make offsets and "unchanged" depend on
    the browser. Raises TypeError for a non-string.
    """
    if not isinstance(raw, str):
        raise TypeError("copy must be a string")
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_text(raw):
    """Title or notes: line endings unified and ends trimmed. Raises TypeError for a non-string."""
    return normalize_copy(raw)


def _is_blank(text):
    return _BLANK.fullmatch(text) is not None


def _title(raw):
    if not isinstance(raw, str):
        return None, "title_required"
    value = normalize_text(raw)
    if _is_blank(value):
        return None, "title_required"
    if len(value) > MAX_TITLE_CHARS:
        return None, "title_long"
    if "\n" in value:
        return None, "title_lines"
    if _CONTROL.search(value):
        return None, "title_chars"
    return value, None


def _copy(raw):
    if not isinstance(raw, str):
        return None, "copy_required"
    value = normalize_copy(raw)
    if _is_blank(value):
        return None, "copy_required"
    if len(value) > MAX_COPY_CHARS:
        return None, "copy_long"
    if _CONTROL.search(value):
        return None, "copy_chars"
    return value, None


def _notes(raw):
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "notes_chars"
    value = normalize_text(raw)
    if _is_blank(value):
        return None, None
    if len(value) > MAX_NOTES_CHARS:
        return None, "notes_long"
    if _CONTROL.search(value):
        return None, "notes_chars"
    return value, None


def _launch(raw, today):
    if not isinstance(raw, str) or raw == "":
        return None, "launch_required"
    if not _ISO_DATE.fullmatch(raw):
        return None, "launch_format"
    try:
        value = date(int(raw[:4]), int(raw[5:7]), int(raw[8:]))
    except ValueError:
        return None, "launch_format"
    if value > today + timedelta(days=MAX_YEARS_AHEAD_DAYS):
        return None, "launch_far"
    if value < today - timedelta(days=MAX_DAYS_BACK):
        return None, "launch_old"
    return value, None


def _choice(raw, allowed, code):
    if isinstance(raw, str) and raw in allowed:
        return raw, None
    return None, code


def launch_warnings(launch, today):
    """["launch_past"], ["launch_rush"] or []: the same rule the queue uses for Overdue and Rush."""
    if launch < today:
        return ["launch_past"]
    if clock.business_days_until(today, launch) <= clock.RUSH_BUSINESS_DAYS:
        return ["launch_rush"]
    return []


def validate_submission(fields, today):
    """Check a new submission's fields. Returns (clean, errors, warnings).

    `clean` holds the cleaned value of every field that passed (launch_date as an ISO string);
    `errors` maps a field name to one of MESSAGES (never the user's value); `warnings` lists
    "launch_past" or "launch_rush" whenever the launch date itself is valid, even if another field
    has an error. Warnings never block. Pure: `today` is passed in, there is no clock or database.
    A missing or non-string value counts as missing.
    """
    clean, errors, warnings = {}, {}, []
    checks = (
        ("title", _title(fields.get("title"))),
        ("product", _choice(fields.get("product"), PRODUCTS, "product")),
        ("channel", _choice(fields.get("channel"), CHANNELS, "channel")),
        ("launch_date", _launch(fields.get("launch_date"), today)),
        ("copy", _copy(fields.get("copy"))),
        ("notes", _notes(fields.get("notes"))),
    )
    for name, (value, code) in checks:
        if code:
            errors[name] = MESSAGES[code]
        elif name == "launch_date":
            clean[name] = value.isoformat()
            warnings.extend(launch_warnings(value, today))
        else:
            clean[name] = value
    return clean, errors, warnings


def warning_text(code, launch_date, today):
    """The sentence for a warning code. Fixed wording plus a number; no user text goes in."""
    if code == "launch_past":
        return "This launch date has already passed. Reviewers will see it as overdue."
    launch = date.fromisoformat(launch_date) if isinstance(launch_date, str) else launch_date
    days = (launch - today).days
    business = clock.business_days_until(today, launch)
    if days == 0:
        when = "Launches today."
    elif business == 0:
        when = "Launches before the next business day."
    else:
        when = "Launches in %d business day%s." % (business, "" if business == 1 else "s")
    return when + " A rush review may not finish in time."


# ---- pre-check ----

def precheck(product, channel, copy):
    """What the rules say about this copy, for the marketer to read before submitting.

    Read-only: it runs the same `rules.evaluate` the stored flags come from, on the same
    normalized copy, so the marketer and the reviewer always see the same flags. Returns
    {"state": "incomplete", "message": ...} when it cannot check yet, else {"state": "ok",
    "cards": [...]} (an empty list means no flags). Never raises on user input.
    """
    from app import review, rules

    if product not in PRODUCTS or channel not in CHANNELS or not isinstance(product, str) \
            or not isinstance(channel, str):
        return {"state": "incomplete", "message": "Choose a product, channel and add copy to see flags."}
    value, code = _copy(copy)
    if code == "copy_required":
        return {"state": "incomplete", "message": "Choose a product, channel and add copy to see flags."}
    if code:
        return {"state": "incomplete", "message": MESSAGES[code]}
    found = rules.evaluate(product, channel, value)
    rows = [{"id": i, "rule_id": f.rule_id, "severity": f.severity, "kind": f.kind,
             "matched_text": f.matched_text, "start_index": f.start, "end_index": f.end}
            for i, f in enumerate(found, 1)]
    return {"state": "ok", "cards": review.cards_view({"flags": rows, "dismissals": []}, [])}


# ---- create ----

class SubmitError(Exception):
    """A refused submission or resubmission. `code` is a fixed word, never text from the request.

    create_submission: capacity, duplicate, bad_marketer. create_version: not_found, not_owner,
    stale_version, not_resubmittable, unchanged, capacity.
    """

    def __init__(self, code, existing_id=None):
        super().__init__(code)
        self.code = code
        self.existing_id = existing_id


def create_submission(conn, fields, submitted_by, now):
    """Create a submission with its first version and flags, and return its id. The only place that does.

    `fields` are the cleaned values from validate_submission (they are checked again here, so a
    caller that skips validation cannot store bad data). Takes the write lock before reading, so
    two identical requests cannot both pass the duplicate check. The submission, version and
    flags are written in one transaction and committed here; any refusal or failure rolls back
    everything. Status, version number, timestamp and flags are decided here, never by the caller.
    """
    from app import seed

    clean, errors, _ = validate_submission(fields, now.date())
    if errors:
        raise ValueError("fields did not validate")
    if not isinstance(submitted_by, str) or submitted_by not in ALL_SUBMITTERS:
        raise SubmitError("bad_marketer")
    if conn.in_transaction:
        raise RuntimeError("create_submission needs a connection with no open transaction")

    stamp = seed.format_timestamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT count(*) FROM submission").fetchone()[0] >= MAX_SUBMISSIONS:
            raise SubmitError("capacity")
        same = conn.execute(
            "SELECT s.id FROM submission s JOIN version v"
            " ON v.submission_id = s.id AND v.version_number = s.current_version"
            " WHERE s.submitted_by = ? AND s.title = ? AND s.product = ? AND s.channel = ? AND v.copy = ?"
            " ORDER BY s.id LIMIT 1",
            (submitted_by, clean["title"], clean["product"], clean["channel"], clean["copy"])).fetchone()
        if same is not None:
            raise SubmitError("duplicate", existing_id=same[0])
        sid = conn.execute(
            "INSERT INTO submission (title, product, channel, status, launch_date, submitted_by, created_at,"
            " current_version) VALUES (?, ?, ?, 'new', ?, ?, ?, 1)",
            (clean["title"], clean["product"], clean["channel"], clean["launch_date"], submitted_by,
             stamp)).lastrowid
        vid = conn.execute(
            "INSERT INTO version (submission_id, version_number, copy, notes, created_at)"
            " VALUES (?, 1, ?, ?, ?)", (sid, clean["copy"], clean.get("notes"), stamp)).lastrowid
        flags.store_flags(conn, vid, flags.evaluate_version(conn, vid))
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return sid


RESUBMITTABLE_STATUSES = ("changes_requested", "rejected")


def _is_int(value):
    return type(value) is int


def create_version(conn, submission_id, base_version, fields, submitted_by, now):
    """Add the next version of a submission and return its number. The only place that does.

    `fields` holds the new copy, notes and launch date (title, product and channel never change
    here; anything else in `fields` is ignored). They are checked again here. Takes the write
    lock before reading, then refuses in this order, writing nothing: no such submission
    (`not_found`); a different marketer (`not_owner`); `base_version` is not the current version,
    which is what a second tab or a double click looks like (`stale_version`); the submission is
    not waiting on the marketer, meaning its status is not changes requested or rejected or the
    current version has no decision (`not_resubmittable`); the copy is the same as the previous
    version's (`unchanged`); too many versions already (`capacity`).

    On success the new version, its flags, the new current version, the status `in_review` and
    the launch date are written in one transaction and committed here. Earlier versions, their
    flags, decisions, dismissals and comments are never touched. Any failure rolls back everything.
    """
    from app import seed

    today = now.date()
    copy, copy_error = _copy(fields.get("copy"))
    notes, notes_error = _notes(fields.get("notes"))
    launch, launch_error = _launch(fields.get("launch_date"), today)
    if copy_error or notes_error or launch_error:
        raise ValueError("fields did not validate")
    if conn.in_transaction:
        raise RuntimeError("create_version needs a connection with no open transaction")

    stamp = seed.format_timestamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status, current_version, submitted_by FROM submission WHERE id = ?",
                           (submission_id,)).fetchone() if _is_int(submission_id) else None
        if row is None:
            raise SubmitError("not_found")
        if not isinstance(submitted_by, str) or submitted_by != row["submitted_by"]:
            raise SubmitError("not_owner")
        current = row["current_version"]
        if not _is_int(base_version) or base_version != current:
            raise SubmitError("stale_version")
        decided = conn.execute("SELECT 1 FROM decision WHERE submission_id = ? AND version_number = ?",
                               (submission_id, current)).fetchone()
        if row["status"] not in RESUBMITTABLE_STATUSES or decided is None:
            raise SubmitError("not_resubmittable")
        previous = conn.execute("SELECT copy FROM version WHERE submission_id = ? AND version_number = ?",
                                (submission_id, current)).fetchone()
        if previous is not None and normalize_copy(previous["copy"]) == copy:
            raise SubmitError("unchanged")
        if current >= MAX_VERSIONS:
            raise SubmitError("capacity")
        number = current + 1
        vid = conn.execute(
            "INSERT INTO version (submission_id, version_number, copy, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (submission_id, number, copy, notes, stamp)).lastrowid
        flags.store_flags(conn, vid, flags.evaluate_version(conn, vid))
        conn.execute("UPDATE submission SET current_version = ?, status = 'in_review', launch_date = ? WHERE id = ?",
                     (number, launch.isoformat(), submission_id))
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return number


# ---- resubmit page ----

def resubmit_block(data, marketer, role):
    """Why the resubmit form is not offered for this item, or None when it is.

    `data` is review.load_review's result. Fixed sentences only; the owner's name comes from the
    allowlist of marketers, never from a request. The server re-checks all of this in
    create_version, so this decides what the page shows, not what is allowed.
    """
    sub = data["submission"]
    if role not in SUBMITTING_ROLES:
        return "Only marketers and affiliate partners resubmit copy. You are viewing the demo as a reviewer."
    if sub["status"] == "approved":
        return "This item is approved and locked. It can't be changed or resubmitted."
    if sub["status"] not in RESUBMITTABLE_STATUSES or data["decision"] is None:
        return "This item is still in review. You can resubmit once a reviewer has replied."
    if sub["submitted_by"] != marketer:
        return "This item belongs to %s. Switch to them to edit it." % sub["submitted_by"]
    if sub["current_version"] >= MAX_VERSIONS:
        return "This item has reached the limit of %d versions for the demo. Reset the demo to start again." % MAX_VERSIONS
    return None


def compare_copy(previous, typed):
    """The marketer's "your changes so far": the typed copy against the previous version's copy.

    Both sides are normalized the same way create_version does, so "unchanged" here is exactly the
    server's `unchanged` refusal. Returns {"state": "unchanged"} or {"state": "too_long"} or
    {"state": "changed", "pieces": [(kind, text), ...], "added": n, "removed": n} (word counts).
    Pure: nothing is read or written. A non-string `typed` counts as empty.
    """
    from app import diff

    old = normalize_copy(previous)
    new = normalize_copy(typed) if isinstance(typed, str) else ""
    if old == new:
        return {"state": "unchanged"}
    pieces = diff.diff_text(old, new)
    if pieces is None:
        return {"state": "too_long"}
    return {"state": "changed", "pieces": pieces, "added": diff.count_words(pieces, "added"),
            "removed": diff.count_words(pieces, "removed")}
