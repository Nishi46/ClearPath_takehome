import json
import re
import subprocess
from pathlib import Path

import pytest

from app.templating import APP_DIR
from tests.test_review_decision_form import form as extract_form

JS = (APP_DIR / "static" / "review.js").read_text()
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
needs_chrome = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")

SCRIPT = """
var submits = [];
window.addEventListener('submit', function (e) {          // runs after review.js (bubbling, on window)
  submits.push({prevented: e.defaultPrevented, data: Object.fromEntries(new FormData(document.querySelector('form')))});
  e.preventDefault();                                      // never really navigate
});
function $(sel) { return document.querySelector(sel); }
function state() {
  return {approve: $('button[value=approved]').disabled, changes: $('button[value=changes_requested]').disabled,
          reject: $('button[value=rejected]').disabled, busy: $('form').getAttribute('aria-busy')};
}
function type(text) { var t = $('textarea'); t.value = text; t.dispatchEvent(new Event('input', {bubbles: true})); }
window.addEventListener('load', function () {
  var r = {};
  r.initial = state();
  type('   \\n\\t '); r.spaces = state();
  type('\\u200b\\u2060'); r.zeroWidth = state();
  type('Add the APR.'); r.typed = state();
  type(''); r.cleared = state();
  type('Add the APR.');
  $('button[value=rejected]').click(); r.afterClick = state();
  $('button[value=rejected]').click(); $('button[value=approved]').click();      // disabled: do nothing
  $('form').requestSubmit();                                                    // a second submit is blocked
  r.submits = submits;
  r.hidden = document.querySelectorAll('input[name=outcome]').length;
  window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
  r.restored = state();
  r.hiddenAfter = document.querySelectorAll('input[name=outcome]').length;
  document.getElementById('out').textContent = JSON.stringify(r);
});
"""


def run_in_chrome(tmp_path, form_html, extra_head=""):
    (tmp_path / "review.js").write_text(JS)
    (tmp_path / "h.html").write_text(
        '<!doctype html><meta charset=utf-8><body>%s<pre id=out></pre><script>%s</script>%s'
        '<script src="review.js" defer></script>' % (form_html, SCRIPT, extra_head))
    out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom", "file://%s/h.html" % tmp_path],
                         capture_output=True, text=True, timeout=60).stdout
    return json.loads(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1).replace("&amp;", "&"))


@needs_chrome
def test_buttons_follow_the_reason_and_double_submit_is_blocked(client, tmp_path):
    r = run_in_chrome(tmp_path, extract_form(client.get("/review/3").text))
    on, off = False, True
    assert r["initial"] == {"approve": on, "changes": off, "reject": off, "busy": None}   # blank at load
    assert r["spaces"]["changes"] is off and r["spaces"]["reject"] is off
    assert r["zeroWidth"]["changes"] is off and r["zeroWidth"]["reject"] is off
    assert r["typed"] == {"approve": on, "changes": on, "reject": on, "busy": None}
    assert r["cleared"]["changes"] is off and r["cleared"]["reject"] is off
    assert r["spaces"]["approve"] is on and r["zeroWidth"]["approve"] is on and r["cleared"]["approve"] is on
    # One click: all buttons locked, the form marked busy.
    assert r["afterClick"] == {"approve": off, "changes": off, "reject": off, "busy": "true"}
    # Further clicks and a second submit reach the page as exactly one real submit.
    real = [s for s in r["submits"] if not s["prevented"]]
    assert len(real) == 1 and len(r["submits"]) == 2 and r["submits"][1]["prevented"] is True
    # The chosen outcome travels in the data even though the button was disabled.
    assert real[0]["data"] == {"version": "1", "reason": "Add the APR.", "outcome": "rejected"}
    assert r["hidden"] == 1
    # Back/forward cache restore re-enables everything and removes the carried field.
    assert r["restored"] == {"approve": on, "changes": on, "reject": on, "busy": None}
    assert r["hiddenAfter"] == 0


@needs_chrome
def test_approve_goes_through_with_no_reason(client, tmp_path):
    form = extract_form(client.get("/review/3").text)
    script = "<script>window.addEventListener('load',function(){var r={};$('button[value=approved]').click();" \
             "r.s=submits;document.getElementById('out').textContent=JSON.stringify(r);});</script>"
    (tmp_path / "review.js").write_text(JS)
    (tmp_path / "h.html").write_text('<!doctype html><meta charset=utf-8><body>%s<pre id=out></pre><script>%s</script>'
                                     '<script src="review.js" defer></script>%s' % (form, SCRIPT.split("window.addEventListener('load'")[0], script))
    out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom", "file://%s/h.html" % tmp_path],
                         capture_output=True, text=True, timeout=60).stdout
    r = json.loads(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1))
    assert len(r["s"]) == 1 and r["s"][0]["data"]["outcome"] == "approved" and r["s"][0]["data"]["reason"] == ""


@needs_chrome
def test_a_page_refilled_after_a_server_error_starts_enabled(client, tmp_path):
    from tests.test_review_decision_route import post
    r = post(client, outcome="rejected", reason="x" * 2001)          # refused: too long, reason kept
    assert r.status_code == 422
    result = run_in_chrome(tmp_path, extract_form(r.text))
    assert result["initial"] == {"approve": False, "changes": False, "reject": False, "busy": None}


# ---- static checks (no browser) ----

def test_script_is_linked_only_when_a_reviewer_has_a_form(client):
    def linked(sid, role=None):
        if role:
            client.cookies.set("role", role)
        return bool(re.search(r'<script src="/static/review\.js\?v=[0-9a-f]{10}" defer></script>',
                              client.get(f"/review/{sid}").text))
    assert linked(3, "reviewer") is True
    # Locked items still take comments, so a reviewer gets the script there too.
    assert linked(6) is True and linked(8) is True and linked(5) is True
    assert linked(3, "marketer") is False and linked(6) is False
    assert "review.js" not in client.get("/").text


def test_no_inline_script_or_handlers_on_the_review_page(client):
    html = client.get("/review/3").text
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son\w+=", html)


def test_script_is_served_from_static(client):
    r = client.get("/static/review.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "eval(" not in JS and "innerHTML" not in JS and "document.write" not in JS


def test_server_does_not_depend_on_the_script(client):
    # Same requests with the script never loaded (this is the plain HTTP path).
    from tests.test_review_decision_route import post
    assert post(client, outcome="rejected", reason="   ").status_code == 422
    assert post(client, outcome="approved").status_code == 303
    assert post(client, outcome="approved").status_code == 409
