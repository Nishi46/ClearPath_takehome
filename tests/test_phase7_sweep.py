"""Phase 7 closing sweep: the documents and the tests agree, the surface did not move, nothing is skipped without a reason.

The heavy checks live in the step files named in STEP_TESTS. This module imports the cheap, high-value ones so
they also run on their own (`pytest tests/test_phase7_sweep.py`), and adds the checks that tie the docs to the code.
"""
import re
from pathlib import Path

import pytest

# Re-run on their own: the route surface, the header and cookie rules, the doc/test matrix, the copy inventory.
from tests.test_phase7_copy import (inventory, test_the_inventory_file_is_current, test_no_copy_sounds_broken_or_leaks_internals,  # noqa: F401
                                    test_names_ids_statuses_and_rules_are_unchanged)
from tests.test_phase7_edge_matrix import (test_every_row_of_the_doc_has_a_test_class,  # noqa: F401
                                           test_every_test_class_maps_to_a_row_of_the_doc)
from tests.test_phase7_security import (test_the_route_table_is_exactly_the_phase_6_surface,  # noqa: F401
                                        test_every_screen_carries_the_security_headers_and_is_never_cached,
                                        test_state_changing_routes_refuse_cross_origin_posts_and_write_nothing,
                                        test_cookie_attributes, test_text_direction_controls_are_refused_in_every_free_text_field)
from tests.test_phase7_first_impression import (test_the_whole_loop_from_the_front_door,  # noqa: F401
                                                test_a_fresh_start_opens_into_a_populated_queue_with_the_urgent_items_first)

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "documentation"

# Every step of phase-7-steps.md, and where it is tested. "manual" steps have a row in the manual checks table.
STEP_TESTS = {
    1: "test_phase7_edge_matrix.py", 2: "test_at_weekday.py",
    3: "test_phase7_states.py", 4: "test_phase7_states.py", 5: "test_phase7_states.py",
    6: "test_phase7_forms.py", 7: "test_phase7_forms.py", 8: "test_phase7_forms.py", 9: "test_phase7_forms.py",
    10: "test_phase7_decisions.py", 11: "test_phase7_decisions.py", 12: "test_phase7_decisions.py",
    13: "test_phase7_decisions.py",
    14: "test_phase7_copy.py", 15: "test_phase7_copy.py", 16: "test_phase7_copy.py",
    17: "test_phase7_polish.py", 18: "test_phase7_polish.py", 19: "test_phase7_polish.py", 20: "test_phase7_polish.py",
    21: "test_phase7_console.py",
    22: "test_phase7_security.py", 23: "test_phase7_security.py", 24: "test_phase7_security.py",
    25: "test_phase7_first_impression.py", 26: "test_phase7_first_impression.py", 27: "test_phase7_sweep.py",
}


def steps_in_doc():
    text = (DOCS / "phase-7-steps.md").read_text()
    return [int(n) for n in re.findall(r"^### Step (\d+):", text, re.M)]


def test_every_step_in_the_plan_has_a_test_file():
    assert steps_in_doc() == list(range(1, 28))
    assert set(STEP_TESTS) == set(steps_in_doc())
    for step, name in STEP_TESTS.items():
        assert (ROOT / "tests" / name).is_file(), (step, name)


def test_every_test_file_of_phase_7_belongs_to_a_step():
    mapped = set(STEP_TESTS.values())
    found = {p.name for p in (ROOT / "tests").glob("test_phase7_*.py")} | {"test_at_weekday.py"}
    assert found == mapped


def test_every_manual_check_has_a_row_in_the_manual_table():
    text = (DOCS / "phase-7-steps.md").read_text()
    table = text[text.index("## Manual checks"):]
    for needle in ("Section 3 matrix", "Reset during an in-progress review", "First-time user", "Second device",
                   "Keyboard-only", "Screen reader", "Console and network"):
        assert needle in table, needle


def test_every_file_named_in_build_md_exists():
    text = (DOCS / "build.md").read_text()
    names = set(re.findall(r"`((?:[\w.-]+/)*[\w.-]+\.(?:py|md|json|html|js|sql|yaml|css))`", text))
    names |= set(re.findall(r"\]\(([\w.-]+\.md)\)", text))
    missing = []
    for name in sorted(names):
        candidates = [ROOT / name, DOCS / name, ROOT / "app" / name, ROOT / "app" / "templates" / name, ROOT / "tests" / name,
                      ROOT / "data" / name]
        if not any(c.exists() for c in candidates):
            missing.append(name)
    assert missing == []


def test_the_documents_agree_on_the_phase_7_status():
    text = (DOCS / "build.md").read_text()
    row = re.search(r"\| 7\. Edge cases, microcopy, polish \| 2 hrs \| (.*?) \|", text).group(1)
    assert row.startswith("Built") and "manual" in row
    assert "## Phase 7: Edge cases, microcopy, polish" in text and "phase-7-steps.md" in text


# A skip is allowed only when it says why. Chrome being absent is the one standing reason.
ALLOWED_OTHER_SKIPS = {"this path also has a read-only GET page; a GET never writes (checked below)",
                       "a raw CR/LF cannot be sent in a cookie header at all",
                       "root ignores file permissions"}


def test_nothing_is_skipped_without_a_stated_reason():
    bad = []
    for path in (ROOT / "tests").glob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text()
        reasons = re.findall(r"pytest\.skip\(\s*\"([^\"]*)\"", text)
        reasons += re.findall(r"skipif\([^\n]*?reason=\"([^\"]*)\"", text)
        reasons += re.findall(r"skipif\(not [^\n]*\n\s*reason=\"([^\"]*)\"", text)
        for reason in reasons:
            if "Chrome" not in reason and reason not in ALLOWED_OTHER_SKIPS:
                bad.append((path.name, reason))
    assert bad == []


def test_the_sweep_files_do_not_use_xfail_to_hide_a_gap():
    for path in (ROOT / "tests").glob("test_phase7_*.py"):
        if path.name != Path(__file__).name:
            assert "xfail" not in path.read_text(), path.name
