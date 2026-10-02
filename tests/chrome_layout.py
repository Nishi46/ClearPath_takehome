"""Render a review page in headless Chrome at an exact viewport size and measure it.

The page is placed in an iframe of the requested size, because headless Chrome will not make its
own window narrower than about 500px. Returns None if Chrome is not installed.
"""
import html as htmllib
import json
import re
import subprocess
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "style.css"

MEASURE = """
function box(d, sel) {
  var e = d.querySelector(sel); if (!e) return null;
  var r = e.getBoundingClientRect();
  return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, sh: e.scrollHeight, ch: e.clientHeight};
}
var f = document.getElementById('f');
f.addEventListener('load', function () {
  var d = f.contentDocument, w = f.contentWindow, de = d.documentElement;
  var res = {innerWidth: w.innerWidth, innerHeight: w.innerHeight, scrollWidth: de.scrollWidth,
             clientWidth: de.clientWidth, scrollHeight: de.scrollHeight};
  ['.review-head h1', '.copy-pane h2', '.copy-text', '.flags-pane h2', '.flag-card', '.decision-buttons',
   '.decision-approve', '.review-main', '.review-side-scroll', '.decision-pane', '.history-strip', '.flag-card h3',
   '.flag-severity', '.snippet-link', '.flag-dismiss summary', '.flag-why'].forEach(function (s) { res[s] = box(d, s); });
  var wide = [];
  d.querySelectorAll('body *').forEach(function (e) { if (e.getBoundingClientRect().right > w.innerWidth + 1) wide.push(e.tagName + '.' + e.className); });
  res.wide = wide.slice(0, 10);
  document.getElementById('out').textContent = JSON.stringify(res);
});
"""


def measure(page_html, width, height):
    if not Path(CHROME).exists():
        return None
    page = re.sub(r'<link rel="stylesheet" href="[^"]*style\.css[^"]*">',
                  "<style>%s</style>" % CSS.read_text(), page_html)
    page = re.sub(r"<script[^>]*></script>", "", page)
    wrapper = ('<!doctype html><meta charset=utf-8><body style="margin:0"><iframe id=f style="width:%dpx;height:%dpx;border:0" '
               'srcdoc="%s"></iframe><pre id=out></pre><script>%s</script>' % (width, height, htmllib.escape(page, quote=True), MEASURE))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "w.html"
        path.write_text(wrapper)
        out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--window-size=1700,1300", "--dump-dom",
                              "file://%s" % path], capture_output=True, text=True, timeout=60).stdout
    return json.loads(htmllib.unescape(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1)))
