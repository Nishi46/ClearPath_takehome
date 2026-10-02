import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.seed import CHANNELS, PRODUCTS

# Fixed path next to the app: nothing from a request can choose which file is loaded.
RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "rules.json"

SEVERITIES = ("high", "medium", "low")
KINDS = ("phrase", "missing")

_REQUIRED_KEYS = ("id", "name", "description", "appliesToProducts", "appliesToChannels",
                  "severity", "kind", "detection", "snippetText")


class RulesError(Exception):
    """The rules file is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    description: str
    products: tuple
    channels: tuple
    severity: str
    kind: str
    detection: object  # read-only mapping
    snippet_text: str


# ---- validation ----

def _text(obj, key, where):
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise RulesError(f"{where}: {key} must be non-blank text")


def _text_list(obj, key, where, required):
    v = obj.get(key)
    if v is None and not required:
        return
    if not isinstance(v, list) or (required and not v):
        raise RulesError(f"{where}: detection.{key} must be a non-empty list of text")
    for item in v:
        if not isinstance(item, str) or not item.strip():
            raise RulesError(f"{where}: detection.{key} has a blank phrase")


def _scope(rule, key, allowed, where):
    v = rule[key]
    if not isinstance(v, list) or not v:
        raise RulesError(f"{where}: {key} must be a non-empty list")
    for item in v:
        if item not in allowed:
            raise RulesError(f"{where}: unknown value {item!r} in {key}")


def validate_rules(data):
    if not isinstance(data, list):
        raise RulesError("Rules file must be a list of rules")
    seen = set()
    for i, rule in enumerate(data):
        if not isinstance(rule, dict):
            raise RulesError(f"Rule #{i + 1} must be an object")
        rid = rule.get("id")
        where = f"Rule {rid}" if isinstance(rid, str) and rid else f"Rule #{i + 1}"
        missing = [k for k in _REQUIRED_KEYS if k not in rule]
        if missing:
            raise RulesError(f"{where} is missing {', '.join(missing)}")
        _text(rule, "id", where)
        if rid in seen:
            raise RulesError(f"{where}: duplicate id")
        seen.add(rid)
        for key in ("name", "description", "snippetText"):
            _text(rule, key, where)
        _scope(rule, "appliesToProducts", PRODUCTS, where)
        _scope(rule, "appliesToChannels", CHANNELS, where)
        if rule["severity"] not in SEVERITIES:
            raise RulesError(f"{where}: unknown severity {rule['severity']!r}")
        if rule["kind"] not in KINDS:
            raise RulesError(f"{where}: unknown kind {rule['kind']!r}")
        det = rule["detection"]
        if not isinstance(det, dict):
            raise RulesError(f"{where}: detection must be an object")
        if rule["kind"] == "phrase":
            _text_list(det, "phrases", where, required=True)
        else:
            _text_list(det, "required", where, required=True)
            _text_list(det, "variants", where, required=False)
            _text_list(det, "satisfiedBy", where, required=False)


# ---- loading ----

def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _build(data):
    return tuple(
        Rule(r["id"], r["name"], r["description"], tuple(r["appliesToProducts"]),
             tuple(r["appliesToChannels"]), r["severity"], r["kind"], _freeze(r["detection"]),
             r["snippetText"])
        for r in data
    )


def load_rules(path=None):
    path = Path(path) if path is not None else RULES_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise RulesError("Rules file not found.") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RulesError(f"Rules file is not valid JSON: {e}") from None
    validate_rules(data)
    return _build(data)


_cache = None


def all_rules():
    """The rules from data/rules.json, loaded once and immutable."""
    global _cache
    if _cache is None:
        _cache = load_rules()
    return _cache


def get_rule(rule_id):
    if isinstance(rule_id, str):
        for rule in all_rules():
            if rule.id == rule_id:
                return rule
    raise RulesError(f"Unknown rule {rule_id!r}")


# ---- text normalization ----

# Stands in for invisible characters (zero-width, soft hyphen, bidi marks). It keeps the text the
# same length, so match offsets still line up with the original copy; the phrase matcher
# treats it as ignorable.
IGNORABLE = ""

_INVISIBLE = ("­᠎​‌‍\u200E\u200F\u202A\u202B\u202C\u202D\u202E"
              "⁠\u2066\u2067\u2068\u2069﻿")
_UNICODE_SPACES = ("           "
                   "    　")
_DASHES = "‐‑‒–—―−﹘﹣－"
_APOSTROPHES = "‘’‚‛′ʼ＇"
_DOUBLE_QUOTES = "“”„‟″＂"
_LINE_BREAKS = "\u0085  "

# Every entry maps one character to exactly one character.
_TABLE = {ord(c): IGNORABLE for c in _INVISIBLE}
_TABLE.update({ord(c): " " for c in _UNICODE_SPACES})
_TABLE.update({ord(c): "-" for c in _DASHES})
_TABLE.update({ord(c): "'" for c in _APOSTROPHES})
_TABLE.update({ord(c): '"' for c in _DOUBLE_QUOTES})
_TABLE.update({ord(c): "\n" for c in _LINE_BREAKS})


def normalize(text):
    """Lowercase and tidy `text` for matching without changing its length.

    Each character is replaced by exactly one character, so an index into the result is the same
    index into the original. Curly quotes and Unicode dashes and spaces become their plain forms;
    invisible characters become IGNORABLE.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    text = text.translate(_TABLE)
    lowered = text.lower()
    if len(lowered) != len(text):
        # A few characters lowercase to more than one (for example a dotted capital I); keep the
        # first so the length does not change.
        lowered = "".join(c.lower()[0] for c in text)
    return lowered


# ---- phrase matching ----

_WORD = re.compile(r"[^\W_]+")
# One separator character: anything that is not a letter or digit (underscore counts as
# punctuation here). IGNORABLE is skipped separately and does not count toward the limit.
_SEP_CHAR = r"(?:[^\w]|_)"
_SKIP = "*"
MAX_GAP = 3


def compile_phrase(phrase):
    """Compile a rule phrase into a regex that runs on normalize()d text.

    The phrase is split into word tokens and each token is escaped, so nothing in a phrase is
    ever treated as a pattern. Between tokens the copy may have 1 to 3 punctuation or space
    characters; a closed-up form is also allowed where the phrase has an apostrophe or hyphen
    ("pre-approved" also matches "preapproved", "you're" also matches "youre"). The phrase must
    start and end on a word boundary, so "act now" does not match inside "react now".
    """
    if not isinstance(phrase, str):
        raise TypeError("phrase must be a string")
    phrase = normalize(phrase)
    tokens = list(_WORD.finditer(phrase))
    if not tokens:
        raise ValueError("phrase has no words")
    parts = []
    for i, tok in enumerate(tokens):
        parts.append(_SKIP.join(re.escape(ch) for ch in tok.group()))
        if i + 1 < len(tokens):
            between = phrase[tok.end():tokens[i + 1].start()]
            closed_up_ok = between != "" and all(c in "-'" for c in between)
            low = 0 if closed_up_ok else 1
            parts.append(f"{_SKIP}(?:{_SEP_CHAR}{_SKIP}){{{low},{MAX_GAP}}}")
    body = "".join(parts)
    return re.compile(rf"(?<![^\W_])(?<![^\W_]){body}(?![^\W_])")


def find_phrase(norm_text, compiled):
    """Return the (start, end) spans of every match in `norm_text`, in order, without overlap.

    Offsets are valid on the original copy because normalize() keeps the length.
    """
    return [m.span() for m in compiled.finditer(norm_text)]


# ---- scope and input guards ----

# Backstop for the engine; the submit form (phase 5) will enforce a much smaller limit.
MAX_COPY_CHARS = 100_000


def _check_choice(value, allowed, label):
    # The message never repeats the value: it came from outside and may end up in logs or pages.
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"Unknown {label}")


def check_inputs(product, channel, copy):
    """Raise unless product, channel and copy are fit to evaluate.

    An unknown product or channel fails loudly: quietly returning no flags would tell a reviewer
    that an item is clean when it was never checked.
    """
    _check_choice(product, PRODUCTS, "product")
    _check_choice(channel, CHANNELS, "channel")
    if not isinstance(copy, str):
        raise TypeError("copy must be a string")
    if len(copy) > MAX_COPY_CHARS:
        raise ValueError(f"Copy is longer than {MAX_COPY_CHARS:,} characters")


def _rule_order(rule):
    digits = re.findall(r"[0-9]+", rule.id)
    return (int(digits[0]) if digits else 0, rule.id)  # so R10 sorts after R9


def rules_for(product, channel):
    """The rules that apply to this product and channel, in rule-id order."""
    _check_choice(product, PRODUCTS, "product")
    _check_choice(channel, CHANNELS, "channel")
    return tuple(sorted(
        (r for r in all_rules() if product in r.products and channel in r.channels),
        key=_rule_order))
