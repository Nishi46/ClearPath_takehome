import unicodedata

# Characters that carry no visible text: control, format (zero-width), and Unicode space separators.
_INVISIBLE = ("Cc", "Cf", "Zs", "Zl", "Zp")


def clean_text(value):
    """The text trimmed, or None if it has no visible characters.

    Beyond the spaces the database CHECKs trim, control, format (zero-width) and Unicode space
    characters count as blank, so a value of only invisible characters is refused. None gives
    None; any other non-string raises TypeError.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("text must be a string or None")
    if not any(unicodedata.category(ch) not in _INVISIBLE for ch in value):
        return None
    return value.strip()
