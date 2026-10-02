"""The submit form in a real browser against a real server (skipped if Chrome is not installed)."""
import pytest

from app import db
from tests.live_browser import HAVE_CHROME, Live

pytestmark = pytest.mark.skipif(not HAVE_CHROME, reason="Google Chrome is not installed here")


@pytest.fixture
def live(db_path):
    from app.main import app

    # uvicorn runs the app's own startup (schema and seed) when the server starts.
    with Live(app) as server:
        yield server


def submissions():
    with db.connect() as c:
        return c.execute("SELECT count(*) FROM submission").fetchone()[0]


def test_typing_is_debounced_and_the_panel_and_warning_update(live):
    lines, r = live.browse("/__harness?s=live")
    assert r["beforePause"] == 0                       # nothing is sent while the typing goes on
    assert r["sentAfterBurst"] == 1                    # one request after the pause, for six keystrokes
    assert "R1: No guaranteed-approval claims" in r["panel"] and "Missing: add this" in r["panel"]
    assert r["indicatorVisible"] == "hidden"           # "Checking..." is gone again
    assert r["copyKept"] == "Guaranteed approval 5"    # the update never touches what was typed
    assert "rush review may not finish in time" in r["rushWarning"]
    assert r["afterFar"].strip() == "" and r["warningNodes"] == 1   # cleared in place, not duplicated
    assert lines == []                                 # no CSP violation, no script error


def test_a_late_answer_does_not_overwrite_a_newer_one(live):
    live.tap.delays = [2.5]                            # the first check answers 2.5 seconds late
    lines, r = live.browse("/__harness?s=race")
    assert r["sent"] == 2
    assert "R1: " not in r["panel"] and "Hello second" not in r["panel"]   # the panel is the second answer
    assert "Missing: add this" in r["panel"]           # (copy "Hello second" has no phrase flag but missing ones)
    # htmx itself traces the abort it was asked for (info level, not an error). Nothing else may appear.
    assert all('"htmx:afterRequest"' in l or '"htmx:sendAbort"' in l for l in lines), lines


def test_a_double_click_sends_one_submission(live):
    before = submissions()
    live.browse("/__harness?s=double", wait_for_result=False, seconds=4)
    assert len(live.tap.posts("/submit")) == 1         # the plain form post: htmx did not take it over
    assert submissions() == before + 1
    assert [e for e in live.tap.posts("/submit/check") if not e[2]] == []   # and no plain check was sent


def test_check_flags_button_is_a_plain_post_to_the_check_route(live):
    before = submissions()
    live.browse("/__harness?s=check", wait_for_result=False, seconds=3)
    plain = [e for e in live.tap.posts("/submit/check") if not e[2]]
    assert len(plain) == 1 and live.tap.posts("/submit") == [] and submissions() == before


@pytest.mark.parametrize("path", ["/submit", "/mine", "/submit?x=1"])
def test_real_pages_log_nothing_to_the_console(live, path):
    # The pages with the real security headers: a CSP violation or script error would be logged.
    lines, _ = live.browse("/__harness?s=none&go=" + path, wait_for_result=False, seconds=3)
    assert ("GET", path.split("?")[0], False) in live.tap.log      # the real page was really loaded
    assert lines == [], lines

