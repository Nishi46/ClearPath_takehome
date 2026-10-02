import unicodedata

from app.textclean import clean_text

# Validation for a flag dismissal note. Pure: no database, clock or network.

MAX_NOTE_CHARS = 1000

CODES = ("note_required", "note_too_long", "note_bad_chars")
MESSAGES = {
    "note_required": "Add a note saying why this flag doesn't apply.",
    "note_too_long": "Keep the note under 1,000 characters.",
    "note_bad_chars": "The note can't contain control characters.",
}


def validate_note(raw):
    """Return `(note, None)` for a good note or `(None, code)` with a code from CODES.

    A non-string (a list, an uploaded file, None) counts as missing. CRLF and lone CR become LF
    so the stored note reads the same in every browser. Control characters other than tab and
    newline are refused, so a note cannot hide or forge lines in the audit trail. The limit
    counts characters after trimming, not bytes.
    """
    if not isinstance(raw, str):
        return None, "note_required"
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n" for ch in text):
        return None, "note_bad_chars"
    note = clean_text(text)
    if note is None:
        return None, "note_required"
    if len(note) > MAX_NOTE_CHARS:
        return None, "note_too_long"
    return note, None
