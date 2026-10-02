import itertools

import pytest

from app import rules
from app.rules import MAX_COPY_CHARS, check_inputs, rules_for
from app.seed import CHANNELS, PRODUCTS

ALL = ("loan", "card", "mortgage")


def ids(product, channel):
    return [r.id for r in rules_for(product, channel)]


def test_scope_matrix_matches_seed_data_doc():
    # seed-data.md section 1, "Applies to" column.
    by_product = {"R1": ALL, "R2": ALL, "R3": ("mortgage",), "R4": ("mortgage", "loan"),
                  "R5": ("loan", "card"), "R6": ALL, "R7": ALL}
    by_channel = {"R1": CHANNELS, "R2": CHANNELS, "R3": CHANNELS, "R4": CHANNELS,
                  "R5": CHANNELS, "R6": CHANNELS, "R7": ("paid_social", "display")}
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        expected = [r for r in sorted(by_product)
                    if product in by_product[r] and channel in by_channel[r]]
        assert ids(product, channel) == expected, (product, channel)


def test_specific_scope_examples():
    assert "R3" not in ids("loan", "email")              # R3 never fires on a loan email
    assert "R3" in ids("mortgage", "email")
    assert "R5" not in ids("mortgage", "display")
    assert "R7" not in ids("loan", "email") and "R7" not in ids("loan", "affiliate_page")
    assert ids("card", "paid_social") == ["R1", "R2", "R5", "R6", "R7"]
    assert ids("mortgage", "display") == ["R1", "R2", "R3", "R4", "R6", "R7"]


def test_every_pair_has_rules_and_every_rule_applies_somewhere():
    seen = set()
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        got = rules_for(product, channel)
        assert got
        seen.update(r.id for r in got)
    assert seen == {r.id for r in rules.all_rules()}


def test_result_is_stable_and_ordered():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        first = rules_for(product, channel)
        assert first == rules_for(product, channel)
        assert [r.id for r in first] == sorted(r.id for r in first)


def test_order_is_numeric_not_alphabetical(monkeypatch):
    base = rules.get_rule("R1")
    fake = tuple(rules.Rule(i, *(getattr(base, f) for f in
                 ("name", "description", "products", "channels", "severity", "kind", "detection",
                  "snippet_text"))) for i in ("R10", "R2", "R1", "R9"))
    monkeypatch.setattr(rules, "_cache", fake)
    assert ids("loan", "email") == ["R1", "R2", "R9", "R10"]


def test_rules_are_the_loaded_objects():
    assert rules_for("loan", "email")[0] is rules.get_rule("R1")


BAD_CHOICES = ["crypto", "", " ", "Loan", "LOAN", "loan ", " loan", "loan\n", "loаn", "mort gage",
               "<script>alert(1)</script>", "'; DROP TABLE submission; --", "x" * 10_000,
               None, 5, 1.5, True, b"loan", ["loan"], ("loan",), {"loan"}, object()]


@pytest.mark.parametrize("bad", BAD_CHOICES)
def test_bad_product_raises_value_error(bad):
    with pytest.raises(ValueError, match="Unknown product"):
        rules_for(bad, "email")
    with pytest.raises(ValueError, match="Unknown product"):
        check_inputs(bad, "email", "copy")


@pytest.mark.parametrize("bad", BAD_CHOICES)
def test_bad_channel_raises_value_error(bad):
    with pytest.raises(ValueError, match="Unknown channel"):
        rules_for("loan", bad)
    with pytest.raises(ValueError, match="Unknown channel"):
        check_inputs("loan", bad, "copy")


def test_error_message_does_not_echo_the_input():
    payload = "<script>alert(1)</script>'; DROP TABLE flag; --"
    for call in (lambda: rules_for(payload, "email"), lambda: rules_for("loan", payload),
                 lambda: check_inputs(payload, "email", "x"), lambda: check_inputs("loan", payload, "x")):
        with pytest.raises(ValueError) as e:
            call()
        assert "script" not in str(e.value) and "DROP" not in str(e.value)


@pytest.mark.parametrize("bad", [None, b"bytes", 5, 1.5, ["a"], ("a",), {"a": 1}, object(), True])
def test_non_string_copy_raises_type_error(bad):
    with pytest.raises(TypeError):
        check_inputs("loan", "email", bad)


def test_copy_length_limit_is_exact():
    check_inputs("loan", "email", "")
    check_inputs("loan", "email", "   \n ")
    check_inputs("loan", "email", "a" * MAX_COPY_CHARS)
    with pytest.raises(ValueError, match="longer than 100,000"):
        check_inputs("loan", "email", "a" * (MAX_COPY_CHARS + 1))
    assert MAX_COPY_CHARS == 100_000


def test_length_counts_characters_not_bytes():
    check_inputs("loan", "email", "\U0001f600" * MAX_COPY_CHARS)  # 400,000 bytes, 100,000 characters


def test_guard_order_product_before_channel_before_copy():
    with pytest.raises(ValueError, match="product"):
        check_inputs("nope", "nope", None)
    with pytest.raises(ValueError, match="channel"):
        check_inputs("loan", "nope", None)


def test_every_valid_pair_passes_the_guard():
    for product, channel in itertools.product(PRODUCTS, CHANNELS):
        check_inputs(product, channel, "Some copy.")
