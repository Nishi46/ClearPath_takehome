import json
import bisect
import functools
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


# ---- evaluation ----

@dataclass(frozen=True)
class Flag:
    """One finding. Names, explanations and snippets are looked up from the rule by `rule_id`.

    Phrase flags carry the matched text and its start and end in the original copy; missing-text
    flags carry None for all three. This is the same shape the `flag` table enforces.
    """
    rule_id: str
    severity: str
    kind: str
    matched_text: object = None
    start: object = None
    end: object = None

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError("Unknown severity")
        if self.kind == "phrase":
            if (not isinstance(self.matched_text, str) or not self.matched_text
                    or type(self.start) is not int or type(self.end) is not int
                    or not 0 <= self.start < self.end):
                raise ValueError("A phrase flag needs matched text and 0 <= start < end")
        elif self.kind == "missing":
            if (self.matched_text, self.start, self.end) != (None, None, None):
                raise ValueError("A missing-text flag has no matched text or offsets")
        else:
            raise ValueError("Unknown kind")


# Rules the engine evaluates so far. Later steps add the rest; the engine never reports a rule
# it has not been tested for.
_EVALUATED = {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}


@functools.lru_cache(maxsize=None)
def _phrase_patterns(rule_id):
    return tuple(compile_phrase(p) for p in get_rule(rule_id).detection["phrases"])


# R6 is not reported when the copy states when the offer ends: an end word (ends, expires,
# through, until, thru) followed within MAX_END_DATE_GAP characters by a date. A date is a month
# with a day ("oct 31", "oct. 31st", "31 october"), or a numeric month/day ("10/31"). Month and
# day are not checked against a calendar. "Ends soon", "ends today" and a bare "ends 31" do not
# count. The date may come before or after the urgency phrase; it only has to be in the copy.
MAX_END_DATE_GAP = 20
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![^\W_])")
_DAY = r"(?:[12][0-9]|3[01]|0?[1-9])(?:st|nd|rd|th)?(?![^\W_])"
_DATE = (rf"(?:(?<![^\W_]){_MONTH}[\s.,]{{1,3}}{_DAY}"
         rf"|(?<![^\W_]){_DAY}[\s.,]{{1,3}}(?:of[\s.,]{{1,3}})?{_MONTH}"
         r"|(?<![0-9/.])(?:1[0-2]|0?[1-9])/(?:[12][0-9]|3[01]|0?[1-9])(?:/(?:[0-9]{4}|[0-9]{2}))?(?![0-9/]))")
_END_WORD = r"(?<![^\W_])(?:ends?|ending|expires?|expiring|through|thru|until)(?![^\W_])"
_END_DATE = re.compile(rf"{_END_WORD}[\s\S]{{0,{MAX_END_DATE_GAP}}}?(?={_DATE})")


def _has_end_date(norm):
    return _END_DATE.search(norm) is not None


def _phrase_flags(rule, copy, norm):
    spans = sorted({span for pat in _phrase_patterns(rule.id) for span in find_phrase(norm, pat)},
                   key=lambda s: (s[0], -s[1]))
    flags, last_end = [], 0
    for start, end in spans:
        if start < last_end:  # overlaps the previous match of this rule: highlight it once
            continue
        flags.append(Flag(rule.id, rule.severity, "phrase", copy[start:end], start, end))
        last_end = end
    return flags


def is_present(norm_text, patterns):
    """True if any of the compiled phrases appears in the normalized text."""
    return any(p.search(norm_text) for p in patterns)


@functools.lru_cache(maxsize=None)
def _required_patterns(rule_id):
    """Phrases that, if any is present, satisfy a missing-text rule.

    A rule lists its accepted forms in `variants` (R5) or `satisfiedBy` (R7); otherwise `required`
    is the text itself (R3). R7's `required` is a description, not text to look for.
    """
    det = get_rule(rule_id).detection
    texts = tuple(det.get("variants", ())) + tuple(det.get("satisfiedBy", ())) or tuple(det["required"])
    return tuple(compile_phrase(t) for t in texts)


# A link counts as a reference to the full terms when it has a scheme or a path:
# "https://x.example", "clearpath.example/card". A bare domain ("clearpath.com") only names the
# site, so it does not count. Labels and TLDs are length-bounded so a long string of dots cannot
# make the search slow.
_URL = re.compile(
    r"(?<![\w@.-])(?:https?://[^\s]"
    r"|(?:[a-z0-9-]{1,63}\.){1,5}[a-z]{2,24}/)")
_URL_SATISFIES = {"R7"}


# R2 triggers only when a percentage sits near the word "rate" or "interest". "Near" means at
# most MAX_RATE_GAP characters between them, with no blank line in between (same paragraph).
MAX_RATE_GAP = 60
_PERCENT = re.compile(r"(?<![\w.])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+) ?(?:%|percent(?![^\W_]))")
_RATE_WORD = re.compile(r"(?<![^\W_])(?:rates?|interest)(?![^\W_])")
_BLANK_LINE = re.compile(r"\n[ \t\r]*\n")


def _near(norm, left_end, right_start):
    gap = right_start - left_end
    return 0 <= gap <= MAX_RATE_GAP and not _BLANK_LINE.search(norm, left_end, right_start)


def _rate_shown(norm):
    """True if a percentage appears within MAX_RATE_GAP characters of "rate" or "interest"."""
    words = [m.span() for m in _RATE_WORD.finditer(norm)]
    if not words:
        return False
    starts = [w[0] for w in words]
    ends = [w[1] for w in words]
    for m in _PERCENT.finditer(norm):
        ps, pe = m.span()
        # Only the nearest rate word on each side can be the closest, so only those are checked.
        i = bisect.bisect_left(starts, pe)
        if i < len(words) and _near(norm, pe, starts[i]):
            return True
        j = bisect.bisect_right(ends, ps) - 1
        if j >= 0 and _near(norm, ends[j], ps):
            return True
    return False


# Missing-text rules that only apply once something in the copy triggers them.
_TRIGGERS = {"R2": _rate_shown}


def _missing_flags(rule, norm):
    trigger = _TRIGGERS.get(rule.id)
    if trigger is not None and not trigger(norm):
        return []
    if is_present(norm, _required_patterns(rule.id)):
        return []
    if rule.id in _URL_SATISFIES and _URL.search(norm):
        return []
    return [Flag(rule.id, rule.severity, "missing")]


def evaluate(product, channel, copy):
    """Return the flags for this copy, ordered by rule id then position.

    Raises ValueError or TypeError for input it cannot check (see check_inputs). It has no
    side effects and reads nothing but its arguments and the loaded rules.
    """
    check_inputs(product, channel, copy)
    norm = normalize(copy)
    flags = []
    for rule in rules_for(product, channel):
        if rule.id not in _EVALUATED:
            continue
        if rule.kind == "phrase":
            if rule.detection.get("unlessStatedEndDate") and _has_end_date(norm):
                continue
            flags.extend(_phrase_flags(rule, copy, norm))
        else:
            flags.extend(_missing_flags(rule, norm))
    return tuple(flags)
