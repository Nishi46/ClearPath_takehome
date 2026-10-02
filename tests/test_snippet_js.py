import json
import re
import subprocess
from pathlib import Path

import pytest

from app.templating import APP_DIR

JS = (APP_DIR / "static" / "review.js").read_text()
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
needs_chrome = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")


def run_page(tmp_path, html, script):
    """Load a server-rendered review page with the real review.js, run `script` on load, return its JSON."""
    html = re.sub(r'<script src="/static/review\.js[^"]*" defer></script>', '<script src="review.js" defer></script>', html)
    html = re.sub(r'<link[^>]*stylesheet[^>]*>', "", html)
    (tmp_path / "review.js").write_text(JS)
    page = html.replace("</body>", "<pre id=out></pre><script>window.addEventListener('load',function(){var r={};"
                        "%s;document.getElementById('out').textContent=JSON.stringify(r);});</script></body>" % script)
    (tmp_path / "p.html").write_text(page)
    out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom", "file://%s/p.html" % tmp_path],
                         capture_output=True, text=True, timeout=60).stdout
    return json.loads(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1).replace("&amp;", "&")
                      .replace("&lt;", "<").replace("&gt;", ">"))


def page(client, sid):
    client.cookies.set("role", "reviewer")
    return client.get("/review/%d" % sid).text


CLICK = """
var box = document.getElementById('comment-text'), form = document.getElementById('comment-form');
function link(rule) { return document.querySelector('a.snippet-link[data-rule=' + rule + ']'); }
function click(rule) { var e = new MouseEvent('click', {bubbles: true, cancelable: true}); link(rule).dispatchEvent(e); return e.defaultPrevented; }
function hidden() { var h = form.querySelectorAll('input[name=rule_id]'); return h.length ? [h.length, h[0].value] : [0, null]; }
function note() { return document.getElementById('snippet-note').textContent; }
"""


@needs_chrome
def test_snippet_fills_the_box_links_the_rule_and_appends(client, tmp_path):
    r = run_page(tmp_path, page(client, 14), CLICK + """
      r.start = box.value;
      r.p1 = click('R2'); r.v1 = box.value; r.h1 = hidden(); r.n1 = note(); r.focus1 = document.activeElement === box;
      r.p2 = click('R3'); r.v2 = box.value; r.h2 = hidden(); r.n2 = note();
      box.value = 'My own words'; r.p3 = click('R7'); r.v3 = box.value;
      box.value = '   \\n '; click('R2'); r.v4 = box.value;
      r.noReload = location.search === '';
    """)
    assert r["start"] == ""
    assert r["p1"] is True  # the click was handled, so the link did not navigate
    from app import rules
    s2, s3, s7 = (rules.describe({"rule_id": x})["snippet"] for x in ("R2", "R3", "R7"))
    assert r["v1"] == s2 and r["h1"] == [1, "R2"] and r["n1"] == "Snippet inserted from R2. Edit before posting."
    assert r["focus1"] is True
    assert r["v2"] == s2 + "\n" + s3 and r["h2"] == [1, "R3"]  # appended, one hidden field reused, latest rule wins
    assert r["n2"] == "Snippet inserted from R3. Edit before posting."
    assert r["v3"] == "My own words\n" + s7  # typed text is never discarded
    assert r["v4"] == s2  # whitespace-only counts as empty
    assert r["noReload"] is True


@needs_chrome
def test_a_snippet_with_markup_stays_inert(client, tmp_path):
    r = run_page(tmp_path, page(client, 14), CLICK + """
      var a = document.createElement('a'); a.className = 'snippet-link'; a.href = '#';
      a.setAttribute('data-rule', 'R2'); a.setAttribute('data-snippet', '<img src=x onerror="window.pwn=1"><b>hi</b>');
      document.body.appendChild(a);
      r.imgs = document.querySelectorAll('img').length; r.bold = document.querySelectorAll('b').length;
      r.pwn = typeof window.pwn;
    """)
    assert r["imgs"] == 0 and r["bold"] == 0 and r["pwn"] == "undefined"
    # The script reads data attributes as text and assigns textarea.value only.
    assert "innerHTML" not in JS and "eval(" not in JS and "document.write" not in JS and "insertAdjacentHTML" not in JS


@needs_chrome
def test_comment_and_dismiss_forms_send_one_request_on_double_click(client, tmp_path):
    r = run_page(tmp_path, page(client, 12), """
      var seen = [];
      window.addEventListener('submit', function (e) { seen.push(e.defaultPrevented); e.preventDefault(); });
      var cf = document.getElementById('comment-form'), df = document.querySelector('form.dismiss-form');
      document.getElementById('comment-text').value = 'hello';
      cf.requestSubmit(); cf.requestSubmit(); cf.requestSubmit();
      r.comment = {seen: seen.slice(), disabled: cf.querySelector('button').disabled, busy: cf.getAttribute('aria-busy')};
      seen.length = 0;
      df.querySelector('textarea').value = 'note';
      df.requestSubmit(); df.requestSubmit();
      r.dismiss = {seen: seen.slice(), disabled: df.querySelector('button').disabled, busy: df.getAttribute('aria-busy')};
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
      r.restored = [cf.querySelector('button').disabled, cf.getAttribute('aria-busy'), df.querySelector('button').disabled];
    """)
    assert r["comment"] == {"seen": [False, True, True], "disabled": True, "busy": "true"}
    assert r["dismiss"] == {"seen": [False, True], "disabled": True, "busy": "true"}
    assert r["restored"] == [False, None, False]  # Back/forward restore does not leave a stuck button


@needs_chrome
def test_the_script_is_harmless_on_a_page_with_only_a_decision_form(client, tmp_path):
    r = run_page(tmp_path, page(client, 3), "r.ok = !!document.querySelector('form.decision-form'); r.snip = document.querySelectorAll('a.snippet-link').length")
    assert r["ok"] is True and r["snip"] >= 0


def test_the_script_is_served_with_the_snippet_logic():
    assert "data-snippet" in JS and "snippet-note" in JS and "pageshow" in JS
