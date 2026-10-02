import copy
import dataclasses
import json
import re
from pathlib import Path

import pytest

from app import rules
from app.rules import RulesError, get_rule, all_rules, load_rules, validate_rules

ROOT = Path(rules.__file__).resolve().parent.parent


def good():
    return json.loads((ROOT / "data" / "rules.json").read_text())


def broken(mutate):
    data = good()
    mutate(data)
    return data


def test_real_file_validates_and_matches_seed_data_doc():
    validate_rules(good())
    got = {r.id: (r.kind, r.severity, r.snippet_text) for r in all_rules()}
    assert list(got) == [f"R{i}" for i in range(1, 8)]
    kinds = {"R1": "phrase", "R2": "missing", "R3": "missing", "R4": "phrase",
             "R5": "missing", "R6": "phrase", "R7": "missing"}
    sev = {"R1": "high", "R2": "high", "R3": "high", "R4": "medium",
           "R5": "medium", "R6": "low", "R7": "medium"}
    for rid, (kind, severity, snippet) in got.items():
        assert kind == kinds[rid] and severity == sev[rid] and snippet.strip()


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d[0].pop("snippetText"), "R1 is missing snippetText"),
    (lambda d: d[0].pop("detection"), "missing detection"),
    (lambda d: d[1].update(id="R1"), "duplicate id"),
    (lambda d: d[0].update(id="  "), "id must be non-blank"),
    (lambda d: d[0].update(appliesToProducts=["crypto"]), "unknown value 'crypto'"),
    (lambda d: d[0].update(appliesToChannels=["Email"]), "unknown value 'Email'"),
    (lambda d: d[0].update(appliesToProducts=[]), "non-empty list"),
    (lambda d: d[0].update(appliesToChannels="email"), "non-empty list"),
    (lambda d: d[0].update(severity="critical"), "unknown severity"),
    (lambda d: d[0].update(kind="regex"), "unknown kind"),
    (lambda d: d[0].update(name="   "), "name must be non-blank"),
    (lambda d: d[0].update(snippetText=""), "snippetText must be non-blank"),
    (lambda d: d[0].update(description=None), "description must be non-blank"),
    (lambda d: d[0]["detection"].update(phrases=[]), "non-empty list"),
    (lambda d: d[0]["detection"].pop("phrases"), "non-empty list"),
    (lambda d: d[0]["detection"].update(phrases=["ok", " "]), "blank phrase"),
    (lambda d: d[0]["detection"].update(phrases=[3]), "blank phrase"),
    (lambda d: d[2]["detection"].update(required=[]), "non-empty list"),
    (lambda d: d[2]["detection"].pop("required"), "non-empty list"),
    (lambda d: d[4]["detection"].update(variants=[""]), "blank phrase"),
    (lambda d: d[6]["detection"].update(satisfiedBy="terms apply"), "non-empty list"),
    (lambda d: d[0].update(detection="phrases"), "detection must be an object"),
    (lambda d: d.__setitem__(0, "R1"), "must be an object"),
])
def test_each_validation_rule(mutate, expect):
    with pytest.raises(RulesError, match=re.escape(expect)):
        validate_rules(broken(mutate))


def test_not_a_list_is_rejected():
    for bad in ({}, "R1", None, 5):
        with pytest.raises(RulesError, match="list of rules"):
            validate_rules(bad)


def test_error_names_the_rule_id():
    with pytest.raises(RulesError, match="Rule R4"):
        validate_rules(broken(lambda d: d[3].update(severity="x")))


def test_malformed_and_missing_files(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[{not json")
    with pytest.raises(RulesError, match="not valid JSON"):
        load_rules(bad)
    binary = tmp_path / "bin.json"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(RulesError, match="not valid JSON"):
        load_rules(binary)
    with pytest.raises(RulesError, match="not found"):
        load_rules(tmp_path / "nope.json")


def test_loaded_rules_are_immutable_and_cached():
    r = get_rule("R6")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.severity = "high"
    with pytest.raises(TypeError):
        r.detection["phrases"] = ()
    with pytest.raises(TypeError):
        r.detection["phrases"][0] = "x"
    assert isinstance(r.products, tuple)
    assert all_rules() is all_rules()


def test_second_call_does_not_reread_the_file(monkeypatch):
    all_rules()
    monkeypatch.setattr(rules, "load_rules", lambda *a: pytest.fail("re-read"))
    assert all_rules() and get_rule("R1")


def test_get_rule():
    assert get_rule("R1").name == "No guaranteed-approval claims"
    for bad in ("R99", "", "r1", None, 1, ["R1"], "R1; DROP TABLE flag"):
        with pytest.raises(RulesError):
            get_rule(bad)


def test_phrase_and_variant_data_for_the_engine():
    assert "subject to credit approval" in get_rule("R5").detection["variants"]
    assert "terms apply" in get_rule("R7").detection["satisfiedBy"]
    assert get_rule("R6").detection["unlessStatedEndDate"] is True


def test_rule_ids_used_by_the_seed_exist():
    seed = json.loads((ROOT / "data" / "seed.json").read_text())
    known = {r.id for r in all_rules()}
    for s in seed["submissions"]:
        used = [d["ruleId"] for d in s["dismissals"]]
        used += [c["ruleId"] for c in s["comments"] if c.get("ruleId")]
        assert set(used) <= known, s["seedId"]


def test_no_dynamic_code_and_fixed_path():
    src = (ROOT / "app" / "rules.py").read_text()
    for banned in ("eval(", "exec(", "pickle", "yaml.load", "compile("):
        assert banned not in src
    assert re.search(r"^RULES_PATH = Path\(__file__\)", src, re.M)
    assert rules.RULES_PATH.is_absolute()
