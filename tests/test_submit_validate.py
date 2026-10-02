import re
from datetime import date

import pytest

from app import submit
from app.submit import MESSAGES, normalize_copy, validate_submission

TODAY = date(2026, 10, 7)  # a Wednesday


def fields(**over):
    base = {"title": "Spring promo", "product": "loan", "channel": "email", "launch_date": "2026-11-20",
            "copy": "Apply today.", "notes": "First draft."}
    base.update(over)
    return base


def check(**over):
    return validate_submission(fields(**over), TODAY)


def test_valid_input_is_clean_with_no_errors_or_warnings():
    clean, errors, warnings = check()
    assert errors == {} and warnings == []
    assert clean == {"title": "Spring promo", "product": "loan", "channel": "email",
                     "launch_date": "2026-11-20", "copy": "Apply today.", "notes": "First draft."}


def test_all_errors_are_returned_at_once():
    _, errors, _ = validate_submission({}, TODAY)
    assert set(errors) == {"title", "product", "channel", "launch_date", "copy"}
    assert errors["copy"] == "Copy can't be empty or only spaces."


BLANKS = ["", "   ", "\t\n \r\n", "​", "​⁠﻿", " ", None, ["x"], 5, b"x"]


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("name", ["title", "copy", "launch_date", "product", "channel"])
def test_required_fields_reject_blank_and_wrong_types(name, blank):
    clean, errors, _ = check(**{name: blank})
    assert name in errors and name not in clean
    assert set(errors) == {name}  # the other fields are still fine


def test_title_length_counts_characters():
    assert check(title="a" * 120)[1] == {}
    assert "title" in check(title="a" * 121)[1]
    assert check(title="\U0001f600" * 120)[1] == {}
    assert "title" in check(title="\U0001f600" * 121)[1]


@pytest.mark.parametrize("title", ["one\ntwo", "one\r\ntwo", "one\rtwo", "bell\x07", "esc\x1b[0m"])
def test_title_must_be_one_clean_line(title):
    assert "title" in check(title=title)[1]


def test_title_is_trimmed():
    assert check(title="  Hello \n")[0]["title"] == "Hello"


def test_copy_length_boundary():
    assert check(copy="a" * 10_000)[1] == {}
    assert check(copy="a" * 10_001)[1]["copy"] == MESSAGES["copy_long"]


def test_copy_line_endings_are_normalized_and_ends_trimmed():
    clean = check(copy="\n\n  Line one\r\nLine two\rLine three\n\n\n")[0]
    assert clean["copy"] == "Line one\nLine two\nLine three"
    assert normalize_copy("a\r\n\r\nb") == "a\n\nb"  # internal blank lines are kept


@pytest.mark.parametrize("bad", ["a\x00b", "a\x07b", "a\x1bb", "a\x7fb", "a\x85b", "a\x0bb", "a\x0cb"])
def test_control_characters_in_copy_are_refused(bad):
    assert check(copy=bad)[1] == {"copy": MESSAGES["copy_chars"]}


def test_tab_unicode_and_invisible_characters_are_kept():
    copy = "Tab\there \u05e9\u05dc\u05d5\u05dd rtl ​ zero-width é \U0001f600"
    assert check(copy=copy)[0]["copy"] == copy
    assert "copy" in check(copy="a \u202e override")[1]      # an override character is refused (phase 7 step 24)


@pytest.mark.parametrize("blank", [None, "", "  \n ", "​"])
def test_blank_notes_are_allowed_and_not_kept(blank):
    clean, errors, _ = check(notes=blank)
    assert errors == {} and clean["notes"] is None


def test_missing_notes_key_is_allowed():
    given = {k: v for k, v in fields().items() if k != "notes"}
    clean, errors, _ = validate_submission(given, TODAY)
    assert errors == {} and clean["notes"] is None


def test_notes_length_and_characters():
    assert check(notes="n" * 2000)[1] == {}
    assert check(notes="n" * 2001)[1] == {"notes": MESSAGES["notes_long"]}
    assert check(notes="x\x00")[1] == {"notes": MESSAGES["notes_chars"]}
    assert "notes" in check(notes=["a"])[1]


BAD_CHOICES = ["Loan", "loan ", "LOAN", "credit", "", ["loan"], "loan' OR '1'='1", "loan\n", None]


@pytest.mark.parametrize("bad", BAD_CHOICES)
def test_product_is_exact_and_the_message_does_not_echo(bad):
    errors = check(product=bad)[1]
    assert errors == {"product": MESSAGES["product"]}


@pytest.mark.parametrize("bad", BAD_CHOICES + ["loan"])
def test_channel_is_exact(bad):
    assert check(channel=bad)[1] == {"channel": MESSAGES["channel"]}


@pytest.mark.parametrize("bad", ["2026-02-30", "2026-13-01", "2026-1-5", "20261002", "2026-W40-5", "２０２６-10-02",
                                 "2026-10-02T00:00", " 2026-10-02", "2026-10-02 ", "0000-01-01", "9999-12-31",
                                 "2026-10-02\n", "٢٠٢٦-١٠-٠٢", "2026/10/02", "abc"])
def test_launch_date_must_be_a_strict_real_date(bad):
    _, errors, warnings = check(launch_date=bad)
    assert set(errors) == {"launch_date"} and warnings == []


def test_launch_date_window():
    assert check(launch_date="2028-10-06")[1] == {}                      # exactly 730 days ahead
    assert check(launch_date="2028-10-07")[1] == {"launch_date": MESSAGES["launch_far"]}
    assert check(launch_date="2025-10-07")[1] == {}                      # exactly 365 days back
    assert check(launch_date="2025-10-06")[1] == {"launch_date": MESSAGES["launch_old"]}


def test_past_date_warns_without_an_error():
    clean, errors, warnings = check(launch_date="2026-10-06")
    assert errors == {} and warnings == ["launch_past"] and clean["launch_date"] == "2026-10-06"


def test_rush_dates_warn_and_a_far_date_does_not():
    assert check(launch_date="2026-10-08")[2] == ["launch_rush"]         # tomorrow (Thursday)
    assert check(launch_date="2026-10-07")[2] == ["launch_rush"]         # today: rush, not past
    assert check(launch_date="2026-10-09")[2] == ["launch_rush"]         # 2 business days
    assert check(launch_date="2026-10-12")[2] == []                      # Monday: 3 business days


def test_rush_follows_business_days_across_weekends():
    thursday, friday = date(2026, 10, 1), date(2026, 10, 2)
    assert thursday.weekday() == 3 and friday.weekday() == 4
    assert validate_submission(fields(launch_date="2026-10-02"), thursday)[2] == ["launch_rush"]
    # Seen from Friday: the next Monday is 1 business day away, Tuesday 2, Wednesday 3.
    assert validate_submission(fields(launch_date="2026-10-05"), friday)[2] == ["launch_rush"]
    assert validate_submission(fields(launch_date="2026-10-06"), friday)[2] == ["launch_rush"]
    assert validate_submission(fields(launch_date="2026-10-07"), friday)[2] == []


def test_warnings_are_computed_even_when_another_field_is_wrong():
    _, errors, warnings = check(launch_date="2026-10-08", title="")
    assert set(errors) == {"title"} and warnings == ["launch_rush"]


def test_warnings_never_appear_in_errors():
    assert check(launch_date="2026-10-08")[1] == {}


def test_warning_text_is_fixed_wording():
    assert submit.warning_text("launch_past", "2026-10-01", TODAY).startswith("This launch date has already passed")
    assert submit.warning_text("launch_rush", "2026-10-08", TODAY) == \
        "Launches in 1 business day. A rush review may not finish in time."
    assert submit.warning_text("launch_rush", "2026-10-07", TODAY).startswith("Launches today.")
    assert "business days" in submit.warning_text("launch_rush", "2026-10-09", TODAY)
    assert submit.warning_text("launch_rush", "2026-10-10", date(2026, 10, 9)).startswith(
        "Launches before the next business day.")


def test_the_validation_functions_read_no_clock_or_database():
    src = open(submit.__file__).read()
    head = src.split("# ---- pre-check ----")[0]
    assert "datetime.now" not in head and "date.today" not in head and "conn" not in head
    assert re.search(r"clock\.(now|today)\(", head) is None
