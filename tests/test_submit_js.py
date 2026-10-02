import json
import re
import subprocess
from pathlib import Path

import pytest

from app.templating import APP_DIR

JS = (APP_DIR / "static" / "submit.js").read_text()
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
needs_chrome = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")

SCRIPT = """
var submits = [];
window.addEventListener('submit', function (e) {          // runs after submit.js (bubbling, on window)
  submits.push({prevented: e.defaultPrevented});
  e.preventDefault();                                      // never really navigate
});
function $(sel) { return document.querySelector(sel); }
function state() {
  return {submit: $('.submit-button').disabled, check: $('.check-flags').disabled, busy: $('form').getAttribute('aria-busy'),
          count: $('#copy-count').textContent};
}
function type(text) { var t = $('textarea[name=copy]'); t.value = text; t.dispatchEvent(new Event('input', {bubbles: true})); }
window.addEventListener('load', function () {
  var r = {};
  r.initial = state();
  type('abc'); r.three = state().count;
  type('1234567'); r.thousands = state().count;
  type('\\ud83d\\ude00\\ud83d\\ude00'); r.emoji = state().count;           // two emoji are two characters, not four
  type('x'.repeat(12345)); r.big = state().count;
  $('.submit-button').click(); r.afterClick = state();
  $('.submit-button').click();                                         // disabled: nothing happens
  $('form').requestSubmit();                                           // a second submit is blocked
  r.submits = submits;
  window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
  r.restored = state();
  window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: false}));
  document.getElementById('out').textContent = JSON.stringify(r);
});
"""


def form_html(client):
    html = client.get("/submit").text
    return re.search(r'<form method="post" action="/submit".*?</form>', html, re.S).group(0)


def run_in_chrome(tmp_path, form):
    (tmp_path / "submit.js").write_text(JS)
    (tmp_path / "h.html").write_text(
        '<!doctype html><meta charset=utf-8><body>%s<pre id=out></pre><script>%s</script>'
        '<script src="submit.js" defer></script>' % (form, SCRIPT))
    out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom", "file://%s/h.html" % tmp_path],
                         capture_output=True, text=True, timeout=60).stdout
    return json.loads(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1).replace("&amp;", "&"))


@needs_chrome
def test_count_double_submit_and_back_button(mclient, tmp_path):
    r = run_in_chrome(tmp_path, form_html(mclient))
    assert r["initial"] == {"submit": False, "check": False, "busy": None, "count": "0 / 10,000 characters"}
    assert r["three"] == "3 / 10,000 characters" and r["thousands"] == "7 / 10,000 characters"
    assert r["emoji"] == "2 / 10,000 characters" and r["big"] == "12,345 / 10,000 characters"
    assert r["afterClick"] == {"submit": True, "check": True, "busy": "true", "count": r["afterClick"]["count"]}
    assert r["submits"] == [{"prevented": False}, {"prevented": True}]    # one real submit; the second was stopped
    assert r["restored"]["submit"] is False and r["restored"]["check"] is False and r["restored"]["busy"] is None


# ---- markup that the script and HTMX rely on ----

def test_script_and_htmx_are_same_origin_and_nothing_is_inline(mclient):
    html = mclient.get("/submit").text
    assert re.search(r'<script src="/static/submit\.js\?v=[0-9a-f]{10}" defer></script>', html)
    assert re.search(r'<script src="/static/htmx\.min\.js\?v=[0-9a-f]{10}"', html)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)          # no inline script
    assert not re.search(r"\son\w+=", html) and ' style="' not in html


def test_htmx_is_set_up_to_debounce_and_replace(mclient):
    form = form_html(mclient)
    assert 'hx-post="/submit/check"' in form and 'hx-trigger="input delay:500ms"' in form
    assert 'hx-sync="this:replace"' in form and 'hx-target="#precheck-body"' in form
    assert "allowEval" in mclient.get("/submit").text and "&quot;allowEval&quot;" not in form


def test_the_form_works_without_the_script_or_htmx(mclient):
    # TestClient runs no JavaScript at all: the plain form posts, validates and checks on the server.
    assert 'formaction="/submit/check"' in form_html(mclient)
    assert mclient.post("/submit/check", data={"product": "loan", "channel": "email", "copy": "Hello"}).status_code == 200
    assert mclient.post("/submit", data={}, follow_redirects=False).status_code == 422


def test_the_fragment_is_escaped_and_has_no_script(mclient):
    r = mclient.post("/submit/check", data={"product": "loan", "channel": "email",
                                             "copy": "<script>alert(1)</script> Guaranteed approval"},
                     headers={"HX-Request": "true"})
    assert "<script" not in r.text and "<html" not in r.text
