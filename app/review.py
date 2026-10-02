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
