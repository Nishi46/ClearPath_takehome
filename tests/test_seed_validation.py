import copy
import json
import re
from pathlib import Path

import pytest

from app import seed
from app.seed import SeedError, load_seed, validate_seed


@pytest.fixture(scope="module")
def real():
    return load_seed()


@pytest.fixture
def data(real):
    return copy.deepcopy(real)


def sub(data, seed_id):
    return next(s for s in data["submissions"] if s["seedId"] == seed_id)


def expect_error(data, match):
    with pytest.raises(SeedError, match=match):
        validate_seed(data)


# ---- the real file ----

def test_real_seed_has_14_submissions_with_ids_1_to_14(real):
    assert [s["seedId"] for s in real["submissions"]] == list(range(1, 15))


def test_real_seed_covers_statuses_products_channels(real):
    subs = real["submissions"]
    assert {s["status"] for s in subs} == set(seed.STATUSES)
    assert {s["product"] for s in subs} == set(seed.PRODUCTS)
    assert {s["channel"] for s in subs} == set(seed.CHANNELS)


def test_real_seed_history_items(real):
    assert len(sub(real, 5)["versions"]) == 2
    assert [d["outcome"] for d in sub(real, 7)["decisions"]] == ["rejected", "approved"]
    assert sub(real, 7)["currentVersion"] == 2
    assert len(sub(real, 13)["dismissals"]) == 1


def test_real_seed_event_ordering(real):
    # Decisions, comments and dismissals are never older than their version; a resubmission follows the decision.
    for s in real["submissions"]:
        created = {v["versionNumber"]: v["createdHoursAgo"] for v in s["versions"]}
        for key in ("decisions", "comments", "dismissals"):
            for item in s[key]:
                assert item["hoursAgo"] <= created[item["versionNumber"]], (s["seedId"], key)
        for d in s["decisions"]:
            if d["versionNumber"] + 1 in created:
                assert d["hoursAgo"] > created[d["versionNumber"] + 1]


def test_validation_does_not_mutate_input(data):
    before = copy.deepcopy(data)
    validate_seed(data)
    assert data == before


# ---- one test per rule: break exactly one thing ----

def test_top_level_must_be_an_object_with_submissions():
    expect_error([], "must be an object")
    expect_error({}, "missing submissions")
    expect_error({"submissions": []}, "non-empty")
    expect_error({"submissions": "x"}, "non-empty")


@pytest.mark.parametrize("key", ["seedId", "title", "product", "channel", "launchOffsetDays", "submittedBy",
                                 "createdHoursAgo", "status", "currentVersion", "versions", "decisions",
                                 "comments", "dismissals"])
def test_missing_submission_key(data, key):
    del data["submissions"][0][key]
    expect_error(data, f"missing .*{key}")


def test_non_object_submission(data):
    data["submissions"][0] = "nope"
    expect_error(data, "must be an object")


def test_duplicate_seed_id(data):
    data["submissions"][1]["seedId"] = 1
    expect_error(data, "duplicate seedId 1")


@pytest.mark.parametrize("bad", [0, -1, "1", 1.5, True, None])
def test_bad_seed_id(data, bad):
    data["submissions"][0]["seedId"] = bad
    expect_error(data, "seedId must be a positive integer")


@pytest.mark.parametrize("key,bad", [("status", "done"), ("status", "NEW"), ("product", "boat"),
                                     ("channel", "radio"), ("product", None)])
def test_unknown_enum(data, key, bad):
    data["submissions"][0][key] = bad
    expect_error(data, f"unknown {key}")


def test_unknown_outcome(data):
    sub(data, 5)["decisions"][0]["outcome"] = "maybe"
    expect_error(data, "unknown outcome")


@pytest.mark.parametrize("key", ["title", "submittedBy"])
@pytest.mark.parametrize("bad", ["", "   ", "\t\n", None, 5])
def test_blank_submission_text(data, key, bad):
    data["submissions"][0][key] = bad
    expect_error(data, f"{key} must be non-blank")


@pytest.mark.parametrize("bad", ["", " \n ", None])
def test_blank_copy(data, bad):
    data["submissions"][0]["versions"][0]["copy"] = bad
    expect_error(data, "copy must be non-blank")


@pytest.mark.parametrize("bad", [1.5, "1", None, True])
def test_bad_launch_offset(data, bad):
    data["submissions"][0]["launchOffsetDays"] = bad
    expect_error(data, "launchOffsetDays")


@pytest.mark.parametrize("bad", [-1, "3", None, True])
def test_bad_hours(data, bad):
    data["submissions"][0]["createdHoursAgo"] = bad
    expect_error(data, "createdHoursAgo")


def test_no_versions(data):
    data["submissions"][0]["versions"] = []
    expect_error(data, "at least one version")


def test_version_numbers_not_contiguous(data):
    sub(data, 5)["versions"][1]["versionNumber"] = 3
    expect_error(data, "version numbers must run")


def test_version_numbers_must_start_at_one(data):
    data["submissions"][0]["versions"][0]["versionNumber"] = 2
    expect_error(data, "version numbers must run")


def test_current_version_not_highest(data):
    sub(data, 5)["currentVersion"] = 1
    expect_error(data, "currentVersion must be the highest")


def test_versions_not_oldest_first(data):
    v = sub(data, 5)["versions"]
    v[0]["createdHoursAgo"], v[1]["createdHoursAgo"] = 40, 120
    expect_error(data, "oldest first")


def test_submission_newer_than_first_version(data):
    sub(data, 1)["createdHoursAgo"] = 1
    expect_error(data, "newer than its first version")


def test_decision_on_missing_version(data):
    sub(data, 5)["decisions"][0]["versionNumber"] = 9
    expect_error(data, "version 9, which does not exist")


def test_comment_on_missing_version(data):
    sub(data, 3)["comments"][0]["versionNumber"] = 2
    expect_error(data, "version 2, which does not exist")


def test_dismissal_on_missing_version(data):
    sub(data, 13)["dismissals"][0]["versionNumber"] = 2
    expect_error(data, "version 2, which does not exist")


def test_two_decisions_on_one_version(data):
    d = sub(data, 5)["decisions"]
    d.append(copy.deepcopy(d[0]))
    expect_error(data, "more than one decision")


@pytest.mark.parametrize("outcome", ["changes_requested", "rejected"])
@pytest.mark.parametrize("bad", [None, "", "  "])
def test_negative_outcomes_need_a_reason(data, outcome, bad):
    s = sub(data, 14)
    s["decisions"][0]["outcome"] = outcome
    s["status"] = outcome
    s["decisions"][0]["reason"] = bad
    expect_error(data, "needs a reason")


def test_approved_may_have_no_reason(data):
    sub(data, 6)["decisions"][0]["reason"] = None
    validate_seed(data)


def test_blank_comment_text_and_author(data):
    sub(data, 3)["comments"][0]["text"] = " "
    expect_error(data, "text must be non-blank")


@pytest.mark.parametrize("bad", ["", "r1", "R", "1", "R1; DROP", 1])
def test_bad_rule_id(data, bad):
    sub(data, 13)["dismissals"][0]["ruleId"] = bad
    expect_error(data, "ruleId")


def test_blank_dismissal_note(data):
    sub(data, 13)["dismissals"][0]["note"] = "  "
    expect_error(data, "note must be non-blank")


def test_dismissal_twice(data):
    x = sub(data, 13)["dismissals"]
    x.append(copy.deepcopy(x[0]))
    expect_error(data, "dismissed twice")


@pytest.mark.parametrize("key,section", [("decisions", 5), ("comments", 3), ("dismissals", 13)])
def test_event_older_than_its_version(data, key, section):
    sub(data, section)[key][0]["hoursAgo"] = 100000
    expect_error(data, "before its version existed")


def test_resubmission_before_decision(data):
    sub(data, 5)["decisions"][0]["hoursAgo"] = 10  # v2 was created 40h ago
    expect_error(data, "created before version 1 was decided")


def test_older_version_without_decision(data):
    sub(data, 5)["decisions"] = []
    expect_error(data, "no decision")


def test_approved_version_cannot_have_a_newer_one(data):
    s = sub(data, 5)
    s["decisions"][0]["outcome"] = "approved"
    expect_error(data, "locked")


@pytest.mark.parametrize("seed_id,bad", [
    (1, "approved"),            # no decision at all
    (1, "changes_requested"),
    (3, "rejected"),
    (6, "new"),                 # approved decision but status new
    (6, "in_review"),
    (6, "rejected"),
    (8, "approved"),
    (14, "approved"),
    (5, "approved"),            # v2 undecided
])
def test_status_inconsistent_with_decisions(data, seed_id, bad):
    sub(data, seed_id)["status"] = bad
    expect_error(data, "status")


# ---- file loading ----

def test_missing_file(tmp_path):
    with pytest.raises(SeedError, match="not found"):
        load_seed(tmp_path / "nope.json")


@pytest.mark.parametrize("content", ["{not json", "", "[1,", "\x00"])
def test_malformed_json(tmp_path, content):
    p = tmp_path / "seed.json"
    p.write_text(content)
    with pytest.raises(SeedError, match="not valid JSON"):
        load_seed(p)


def test_invalid_utf8(tmp_path):
    p = tmp_path / "seed.json"
    p.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SeedError):
        load_seed(p)


def test_error_message_does_not_leak_the_file_path(tmp_path):
    with pytest.raises(SeedError) as e:
        load_seed(tmp_path / "secret-dir" / "nope.json")
    assert "secret-dir" not in str(e.value)


def test_load_seed_validates(tmp_path, real):
    bad = copy.deepcopy(real)
    bad["submissions"][0]["status"] = "bogus"
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(SeedError, match="unknown status"):
        load_seed(p)


# ---- security ----

def test_seed_path_is_a_module_constant_inside_the_repo():
    assert seed.SEED_PATH == Path(seed.__file__).resolve().parent.parent / "data" / "seed.json"
    assert seed.SEED_PATH.exists()


def test_no_unsafe_loaders_in_seed_module():
    source = Path(seed.__file__).read_text()
    for banned in (r"\beval\(", r"\bexec\(", r"\bpickle\b", r"yaml\.load", r"\bmarshal\b", r"__import__"):
        assert not re.search(banned, source), banned
