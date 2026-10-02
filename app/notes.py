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


def _validate(raw, limit, required, too_long, bad_chars):
    if not isinstance(raw, str):
        return None, required
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n" for ch in text):
        return None, bad_chars
    clean = clean_text(text)
    if clean is None:
        return None, required
    if len(clean) > limit:
        return None, too_long
    return clean, None


def validate_note(raw):
    """Return `(note, None)` for a good note or `(None, code)` with a code from CODES.

    A non-string (a list, an uploaded file, None) counts as missing. CRLF and lone CR become LF
    so the stored note reads the same in every browser. Control characters other than tab and
    newline are refused, so a note cannot hide or forge lines in the audit trail. The limit
    counts characters after trimming, not bytes.
    """
    return _validate(raw, MAX_NOTE_CHARS, "note_required", "note_too_long", "note_bad_chars")


MAX_COMMENT_CHARS = 2000

COMMENT_CODES = ("text_required", "text_too_long", "text_bad_chars")
COMMENT_MESSAGES = {
    "text_required": "Write a comment before posting.",
    "text_too_long": "Keep the comment under 2,000 characters.",
    "text_bad_chars": "The comment can't contain control characters.",
}


def validate_comment(raw):
    """Same rules as validate_note for a reviewer comment, with a 2,000-character limit.

    Returns `(text, None)` or `(None, code)` with a code from COMMENT_CODES.
    """
    return _validate(raw, MAX_COMMENT_CHARS, "text_required", "text_too_long", "text_bad_chars")
