import json
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
