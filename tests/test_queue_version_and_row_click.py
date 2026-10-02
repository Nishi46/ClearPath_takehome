import re
import shutil
import subprocess
import sqlite3
from pathlib import Path

import pytest

from app.security import CSP
from app.templating import APP_DIR
from tests.test_queue_page import body_rows, row_for

JS = (APP_DIR / "static" / "queue.js").read_text()
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


# ---- version column ----

def test_version_column_shows_the_current_version(client):
    html = client.get("/").text
    assert '<td data-label="Version">v2</td>' in row_for(html, 5)    # two versions
    assert '<td data-label="Version">v2</td>' in row_for(html, 7)    # reject, then approved
    assert '<td data-label="Version">v1</td>' in row_for(html, 1)


def test_version_is_the_last_column_and_each_row_has_one(client):
    html = client.get("/").text
    assert html.count('data-label="Version"') == 14
    assert re.findall(r'<th scope="col"[^>]*>(.*?)</th>', html)[-1] == "Version"
    for row in body_rows(html):
        assert re.findall(r'<td data-label="([^"]+)"', row)[-1] == "Version"


def test_only_seeded_two_version_items_show_v2(client):
    html = client.get("/").text
    rows_v2 = [int(i) for r in body_rows(html) if 'data-label="Version">v2<' in r
               for i in re.findall(r'href="/review/(\d+)"', r)]
    assert sorted(rows_v2) == [5, 7]


def test_a_new_version_updates_the_queue(client, db_path):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT INTO version (submission_id, version_number, copy, created_at) VALUES (3, 2, 'again', 'x')")
    c.execute("UPDATE submission SET current_version = 2 WHERE id = 3")
    c.commit()
    c.close()
    assert '>v2<' in row_for(client.get("/").text, 3)


# ---- row click: what the server sends ----

def test_script_is_external_versioned_and_deferred(client):
    html = client.get("/").text
    tag = re.search(r"<script[^>]*queue\.js[^>]*>", html).group(0)
    assert re.search(r'src="/static/queue\.js\?v=[0-9a-f]{10}"', tag) and "defer" in tag
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)       # still no inline scripts: the CSP holds
    assert "script-src 'self'" in CSP


def test_script_is_served_with_a_javascript_type_and_revalidation(client):
    r = client.get("/static/queue.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"


def test_script_is_only_on_the_queue_not_every_page(client):
    assert "queue.js" in client.get("/").text
    assert "queue.js" not in client.get("/reset/confirm").text
    assert "queue.js" not in client.get("/nope").text


def test_titles_stay_real_links_so_keyboard_and_no_js_users_lose_nothing(client):
    for row in body_rows(client.get("/").text):
        assert re.search(r'<td data-label="Title"><a href="/review/\d+">', row)


def test_script_source_is_free_of_dangerous_patterns():
    for banned in (r"\beval\(", r"\bFunction\(", r"innerHTML", r"document\.write", r"setTimeout\(\s*[\"']", r"\.location\s*=", r"XMLHttpRequest", r"fetch\("):
        assert not re.search(banned, JS), banned
    assert "link.click()" in JS and "a[href]" in JS      # follows the row's own real link, never a computed URL


def test_script_leaves_modified_and_interactive_clicks_alone():
    for guard in ("event.button !== 0", "event.metaKey", "event.ctrlKey", "event.shiftKey", "event.altKey",
                  "a, button, input, select, textarea, label", "getSelection"):
        assert guard in JS


def test_pointer_cursor_only_applies_once_the_script_has_run(client):
    css = (APP_DIR / "static" / "style.css").read_text()
    assert "table.queue.row-clickable tbody tr { cursor: pointer; }" in css
    assert "row-clickable" not in client.get("/").text      # the server never sets it; only queue.js does


# ---- row click: behavior in a real browser ----

HARNESS = """<!doctype html><meta charset=utf-8><body>
<table class="queue"><thead><tr><th id=head>Title</th></tr></thead><tbody>
<tr id=r1><td id=c1><a href="/review/1">One</a></td><td id=c2>Loan</td><td id=btn><button id=b>x</button></td>
    <td><select id=sel><option>a</option></select></td><td id=txt>some selectable text</td></tr>
<tr id=r2><td id=c3>No link in this row</td></tr>
</tbody></table>
<pre id=out></pre>
<script>
var log = [];
// Record where a click on a link would navigate, and stop the real navigation.
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a');
  if (a) { log.push(a.getAttribute('href')); e.preventDefault(); }
}, true);
function click(id, opts) {
  log = [];
  document.getElementById(id).dispatchEvent(new MouseEvent('click', Object.assign({bubbles: true, cancelable: true, button: 0}, opts || {})));
  return log.join(',');
}
window.addEventListener('load', function () {
  var r = {};
  r.cursorClass = document.querySelector('table.queue').classList.contains('row-clickable');
  r.cell = click('c2');
  r.row = click('r1');
  r.textCell = click('txt');
  r.link = click('c1').indexOf('/review/1');
  r.linkOnly = click('c1');
  r.button = click('b');
  r.select = click('sel');
  r.header = click('head');
  r.noLinkRow = click('c3');
  r.ctrl = click('c2', {ctrlKey: true});
  r.meta = click('c2', {metaKey: true});
  r.shift = click('c2', {shiftKey: true});
  r.right = click('c2', {button: 2});
  var range = document.createRange(); range.selectNodeContents(document.getElementById('txt'));
  var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  r.withSelection = click('c2');
  sel.removeAllRanges();
  r.afterSelection = click('c2');
  document.getElementById('out').textContent = JSON.stringify(r);
});
</script>
<script src="queue.js" defer></script>
"""


@pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")
def test_row_click_behaviour_in_a_real_browser(tmp_path):
    (tmp_path / "queue.js").write_text(JS)
    (tmp_path / "harness.html").write_text(HARNESS)
    out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                          "file://%s/harness.html" % tmp_path], capture_output=True, text=True, timeout=60).stdout
    import json
    result = json.loads(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1))
    assert result["cursorClass"] is True                 # the script marked the table as clickable
    assert result["cell"] == "/review/1"                 # click on another cell follows the title link
    assert result["row"] == "/review/1"                  # click on the row itself
    assert result["textCell"] == "/review/1"
    assert result["linkOnly"] == "/review/1"             # click on the link: exactly one navigation, not two
    assert result["button"] == "" and result["select"] == ""     # other controls are left alone
    assert result["header"] == ""                        # the header row is not clickable
    assert result["noLinkRow"] == ""                     # a row without a link does nothing
    assert result["ctrl"] == "" and result["meta"] == "" and result["shift"] == ""   # new-tab clicks are not hijacked
    assert result["right"] == ""
    assert result["withSelection"] == ""                 # finishing a text selection does not navigate
    assert result["afterSelection"] == "/review/1"       # and it works again once nothing is selected
