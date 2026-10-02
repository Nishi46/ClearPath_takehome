import json
import re
from pathlib import Path

# Fixed path next to the app: nothing from a request can choose which file is loaded.
SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed.json"

PRODUCTS = ("loan", "card", "mortgage")
CHANNELS = ("email", "paid_social", "affiliate_page", "display")
STATUSES = ("new", "in_review", "changes_requested", "approved", "rejected")
OUTCOMES = ("approved", "changes_requested", "rejected")
RULE_ID = re.compile(r"R[0-9]+")

# A decision on the current version fixes the status; with no decision the item is still waiting.
STATUS_FOR_OUTCOME = {o: o for o in OUTCOMES}
UNDECIDED_STATUSES = ("new", "in_review")


class SeedError(Exception):
    """The seed file is missing, malformed or inconsistent."""


def load_seed(path=None):
    path = Path(path) if path is not None else SEED_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SeedError("Seed file not found.") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SeedError(f"Seed file is not valid JSON: {e}") from None
    validate_seed(data)
    return data


# ---- validation helpers ----

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _require(obj, keys, where):
    if not isinstance(obj, dict):
        raise SeedError(f"{where} must be an object")
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SeedError(f"{where} is missing {', '.join(missing)}")


def _text(obj, key, where):
    v = obj[key]
    if not isinstance(v, str) or not v.strip():
        raise SeedError(f"{where}: {key} must be non-blank text")


def _choice(obj, key, allowed, where):
    if obj[key] not in allowed:
        raise SeedError(f"{where}: unknown {key} {obj[key]!r}")


def _hours(obj, key, where):
    v = obj[key]
    if not _is_num(v) or v < 0:
        raise SeedError(f"{where}: {key} must be a number of hours, 0 or more")


def validate_seed(data):
    """Raise SeedError naming the first problem; return None when the data is consistent."""
    _require(data, ["submissions"], "seed file")
    subs = data["submissions"]
    if not isinstance(subs, list) or not subs:
        raise SeedError("submissions must be a non-empty list")

    seen = set()
    for s in subs:
        _validate_submission(s)
        if s["seedId"] in seen:
            raise SeedError(f"duplicate seedId {s['seedId']}")
        seen.add(s["seedId"])


def _validate_submission(s):
    _require(s, ["seedId", "title", "product", "channel", "launchOffsetDays", "submittedBy",
                 "createdHoursAgo", "status", "currentVersion", "versions", "decisions",
                 "comments", "dismissals"], "a submission")
    if not _is_int(s["seedId"]) or s["seedId"] < 1:
        raise SeedError("seedId must be a positive integer")
    where = f"submission {s['seedId']}"
    _text(s, "title", where)
    _text(s, "submittedBy", where)
    _choice(s, "product", PRODUCTS, where)
    _choice(s, "channel", CHANNELS, where)
    _choice(s, "status", STATUSES, where)
    if not _is_int(s["launchOffsetDays"]):
        raise SeedError(f"{where}: launchOffsetDays must be a whole number")
    _hours(s, "createdHoursAgo", where)

    versions = _validate_versions(s, where)
    decided = _validate_decisions(s, versions, where)
    _validate_comments(s, versions, where)
    _validate_dismissals(s, versions, where)
    _validate_status(s, decided, where)


def _validate_versions(s, where):
    versions = s["versions"]
    if not isinstance(versions, list) or not versions:
        raise SeedError(f"{where}: needs at least one version")
    times = {}
    previous_time = None
    for i, v in enumerate(versions, start=1):
        _require(v, ["versionNumber", "createdHoursAgo", "copy"], f"{where} version")
        if v["versionNumber"] != i or not _is_int(v["versionNumber"]):
            raise SeedError(f"{where}: version numbers must run 1, 2, 3... in order")
        vw = f"{where} v{i}"
        _text(v, "copy", vw)
        if v.get("notes") is not None and not isinstance(v["notes"], str):
            raise SeedError(f"{vw}: notes must be text")
        _hours(v, "createdHoursAgo", vw)
        if previous_time is not None and v["createdHoursAgo"] >= previous_time:
            raise SeedError(f"{where}: versions must be ordered oldest first")
        previous_time = v["createdHoursAgo"]
        times[i] = v["createdHoursAgo"]
    if s["currentVersion"] != len(versions):
        raise SeedError(f"{where}: currentVersion must be the highest version ({len(versions)})")
    if s["createdHoursAgo"] < times[1]:
        raise SeedError(f"{where}: submission cannot be newer than its first version")
    return times


def _version_time(times, item, where):
    n = item["versionNumber"]
    if n not in times:
        raise SeedError(f"{where}: refers to version {n}, which does not exist")
    return times[n]


def _validate_decisions(s, times, where):
    decided = {}
    for d in s["decisions"]:
        _require(d, ["versionNumber", "outcome", "reviewer", "reason", "hoursAgo"], f"{where} decision")
        dw = f"{where} decision"
        created = _version_time(times, d, dw)
        _choice(d, "outcome", OUTCOMES, dw)
        _text(d, "reviewer", dw)
        _hours(d, "hoursAgo", dw)
        if d["outcome"] != "approved":
            if not isinstance(d["reason"], str) or not d["reason"].strip():
                raise SeedError(f"{dw}: {d['outcome']} needs a reason")
        elif d["reason"] is not None and not isinstance(d["reason"], str):
            raise SeedError(f"{dw}: reason must be text")
        if d["versionNumber"] in decided:
            raise SeedError(f"{where}: more than one decision on version {d['versionNumber']}")
        if d["hoursAgo"] > created:
            raise SeedError(f"{dw}: decided before its version existed")
        decided[d["versionNumber"]] = d
    # An older version can only have been superseded by a resubmission, which
    # requires it to have been sent back, and the resubmission comes after the decision.
    for n, d in decided.items():
        if n < s["currentVersion"]:
            if d["outcome"] == "approved":
                raise SeedError(f"{where}: approved version {n} is locked, so it cannot have a newer version")
            if d["hoursAgo"] <= times[n + 1]:
                raise SeedError(f"{where}: version {n + 1} was created before version {n} was decided")
    for n in range(1, s["currentVersion"]):
        if n not in decided:
            raise SeedError(f"{where}: version {n} has a newer version but no decision")
    return decided


def _validate_comments(s, times, where):
    for c in s["comments"]:
        _require(c, ["versionNumber", "author", "text", "ruleId", "hoursAgo"], f"{where} comment")
        cw = f"{where} comment"
        created = _version_time(times, c, cw)
        _text(c, "author", cw)
        _text(c, "text", cw)
        _hours(c, "hoursAgo", cw)
        if c["ruleId"] is not None and not (isinstance(c["ruleId"], str) and RULE_ID.fullmatch(c["ruleId"])):
            raise SeedError(f"{cw}: ruleId must be null or like R1")
        if c["hoursAgo"] > created:
            raise SeedError(f"{cw}: written before its version existed")


def _validate_dismissals(s, times, where):
    seen = set()
    for x in s["dismissals"]:
        _require(x, ["versionNumber", "ruleId", "note", "dismissedBy", "hoursAgo"], f"{where} dismissal")
        xw = f"{where} dismissal"
        created = _version_time(times, x, xw)
        _text(x, "note", xw)
        _text(x, "dismissedBy", xw)
        _hours(x, "hoursAgo", xw)
        if not (isinstance(x["ruleId"], str) and RULE_ID.fullmatch(x["ruleId"])):
            raise SeedError(f"{xw}: ruleId must look like R1")
        if x["hoursAgo"] > created:
            raise SeedError(f"{xw}: dismissed before its version existed")
        key = (x["versionNumber"], x["ruleId"])
        if key in seen:
            raise SeedError(f"{xw}: {x['ruleId']} dismissed twice on version {x['versionNumber']}")
        seen.add(key)


def _validate_status(s, decided, where):
    current = decided.get(s["currentVersion"])
    if current is None:
        if s["status"] not in UNDECIDED_STATUSES:
            raise SeedError(f"{where}: status {s['status']!r} needs a decision on the current version")
    elif s["status"] != STATUS_FOR_OUTCOME[current["outcome"]]:
        raise SeedError(f"{where}: status {s['status']!r} does not match the {current['outcome']} decision")
