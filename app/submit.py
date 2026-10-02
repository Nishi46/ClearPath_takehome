import re
from datetime import date, timedelta

from app import clock, flags
from app.choices import CHANNELS, PRODUCTS
from app.roles import MARKETERS

# Submitting a piece of copy for review. Pure checks first (normalize and validate), then the
# read-only pre-check, then create_submission, the only code that creates a submission. Every
# statement binds its values as parameters; nothing from a form is ever part of SQL text.

MAX_TITLE_CHARS = 120
MAX_COPY_CHARS = 10_000
MAX_NOTES_CHARS = 2_000
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
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
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
    """A refused submission. `code` is one of capacity, duplicate, bad_marketer; fixed text only."""

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
    if not isinstance(submitted_by, str) or submitted_by not in MARKETERS:
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
