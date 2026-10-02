import itertools
import json
import time
from pathlib import Path

import pytest

from app import rules
from app.rules import Flag, evaluate
from app.seed import CHANNELS, PRODUCTS

ROOT = Path(rules.__file__).resolve().parent.parent
MISSING = lambda rid, sev: Flag(rid, sev, "missing")  # noqa: E731


def fired(rule_id, product, channel, copy):
    return [f for f in evaluate(product, channel, copy) if f.rule_id == rule_id]


def seed_items():
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    for s in data["submissions"]:
        for v in s["versions"]:
            yield s["seedId"], v["versionNumber"], s["product"], s["channel"], v["copy"]


SEED = {(i, v): (p, c, copy) for i, v, p, c, copy in seed_items()}

# ---- R5 ----


@pytest.mark.parametrize("key", [(1, 1), (2, 1), (5, 1), (7, 1)])
def test_r5_fires_on_seed_items_without_the_disclaimer(key):
    assert fired("R5", *SEED[key]) == [MISSING("R5", "medium")]


@pytest.mark.parametrize("key", [(4, 1), (5, 2), (7, 2), (8, 1), (10, 1), (11, 1), (13, 1)])
def test_r5_quiet_when_the_disclaimer_is_present(key):
    assert fired("R5", *SEED[key]) == []


@pytest.mark.parametrize("key", [(3, 1), (6, 1), (9, 1), (12, 1), (14, 1)])
def test_r5_does_not_apply_to_mortgage(key):
    assert fired("R5", *SEED[key]) == []


@pytest.mark.parametrize("text", [
    "Subject to credit approval.", "SUBJECT TO CREDIT APPROVAL", "subject to credit approval",
    "Subject  to   credit approval", "Subject to credit\napproval", "Subject to credit review.",
    "Credit approval required.", "Credit approval is required.", "subject to credit-approval",
    "Subject to credit approval​.", "Subject to credit approval",
])
def test_r5_accepted_forms(text):
    assert fired("R5", "loan", "email", f"Apply now. {text}") == []


@pytest.mark.parametrize("text", [
    "subject to approval", "credit approval", "Subject to credit", "approval required",
    "subject to credit approvals", "not subject to credit approval maybe".replace("subject to credit approval", "subject to credit"),
    "subjecttocreditapproval", "Credit is subject to nothing",
])
def test_r5_near_misses_still_fire(text):
    assert fired("R5", "card", "email", f"Apply now. {text}") == [MISSING("R5", "medium")]


def test_r5_scope_matrix():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        assert bool(fired("R5", product, channel, "Nothing.")) == (product in ("loan", "card"))


def test_r5_negation_is_not_understood_so_it_counts_as_present():
    # The engine reads words, not intent; documented limit.
    assert fired("R5", "loan", "email", "This offer is not subject to credit approval.") == []


# ---- R7 ----


@pytest.mark.parametrize("key", [(2, 1), (4, 1)])
def test_r7_fires_on_short_format_seed_items(key):
    assert fired("R7", *SEED[key]) == [MISSING("R7", "medium")]


def test_r7_fires_on_seed_14():
    assert fired("R7", *SEED[(14, 1)]) == [MISSING("R7", "medium")]


@pytest.mark.parametrize("key", [(7, 1), (7, 2), (10, 1)])
def test_r7_quiet_when_terms_are_referenced(key):
    assert fired("R7", *SEED[key]) == []


@pytest.mark.parametrize("text", [
    "Terms apply.", "TERMS APPLY", "terms and conditions apply", "Terms and conditions apply.",
    "See full terms at the link.", "Full terms available online", "See full terms",
    "Learn more at https://clearpath.example/terms", "http://x.co", "Details: clearpath.example/card",
    "clearpath.example/", "go to www.clearpath.example/terms", "see sub.domain.example.co.uk/legal",
    "(clearpath.example/card)", "Terms apply", "Terms\napply",
])
def test_r7_accepted_forms(text):
    assert fired("R7", "card", "display", f"Great card. {text}") == []


@pytest.mark.parametrize("text", [
    "terms", "apply today", "Conditions may apply", "terms may apply", "Terms of use", "clearpath.com",
    "clearpath.example", "see our site", "email me at a@b.example/x", "3.5%/year", "and/or", "v1.2/x",
    "http:/ /broken", "ftp://x.example", "a link to more info",
])
def test_r7_near_misses_still_fire(text):
    assert fired("R7", "card", "display", f"Great card. {text}") == [MISSING("R7", "medium")]


def test_r7_scope_matrix():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        assert bool(fired("R7", product, channel, "Nothing.")) == (channel in ("paid_social", "display"))


def test_r7_never_fires_for_email_or_affiliate_page():
    for product in PRODUCTS:
        for channel in ("email", "affiliate_page"):
            assert fired("R7", product, channel, "No terms anywhere") == []


def test_bare_domain_is_not_a_reference_to_terms_but_a_path_is():
    assert len(fired("R7", "card", "display", "Visit clearpath.example today")) == 1
    assert fired("R7", "card", "display", "Visit clearpath.example/legal today") == []


@pytest.mark.parametrize("copy", [
    "a." * 50_000, "a-" * 49_000 + ".b", ("a." * 60 + " ") * 800, "http" * 24_000, "x" * 100_000,
    "a.b.c.d.e.f.g.h.i.j.k.l.m.n.o.p " * 3000, "." * 100_000, "@a.b.c.d.e.f.g " * 6000,
])
def test_url_search_is_fast_on_hostile_input(copy):
    start = time.perf_counter()
    evaluate("card", "display", copy)
    assert time.perf_counter() - start < 1.0


def test_hostile_text_is_data():
    assert len(fired("R7", "card", "display", "<script>alert(1)</script>'; DROP TABLE flag; --")) == 1
    assert fired("R7", "card", "display", "<a href='https://x.example/terms'>t</a>") == []


# ---- together ----


def test_card_paid_social_seed_2_has_r5_and_r7_only_so_far():
    ids = [f.rule_id for f in evaluate(*SEED[(2, 1)])]
    assert ids == ["R5", "R7"]


def test_ordering_across_phrase_and_missing_flags():
    flags = evaluate("loan", "display", "Guaranteed approval!")
    assert [f.rule_id for f in flags] == ["R1", "R5", "R7"]


def test_whitespace_only_copy_fires_each_applicable_missing_rule():
    assert [f.rule_id for f in evaluate("card", "display", "   ")] == ["R5", "R7"]
    assert [f.rule_id for f in evaluate("mortgage", "email", "")] == ["R3"]
    assert [f.rule_id for f in evaluate("loan", "email", "")] == ["R5"]
