"""Drive headless Chrome against a real running copy of the app.

The app runs under uvicorn on a free port, wrapped in a small "tap" that logs every request,
can delay the first answers from /submit/check (to make a late response), and serves a test page
at /__harness that loads the real form, the real htmx and the real submit.js. The harness page
reports back by posting to /__result. Chrome's own console output is captured so a test can check
that nothing was logged (CSP violations, script errors).
"""
import asyncio
import json
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HAVE_CHROME = Path(CHROME).exists()

# The real app's policy, except that the harness's own inline script is allowed. Styles stay strict,
# so an inline style written by a script would still be refused and logged.
HARNESS_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self'"

HARNESS = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="htmx-config" content='{"includeIndicatorStyles": false, "allowEval": false}'>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/htmx.min.js"></script></head>
<body><main id="root"></main><pre id="out"></pre>
<script>
document.cookie = "role=marketer; path=/";
var scenario = new URLSearchParams(location.search).get("s"), go = new URLSearchParams(location.search).get("go");
if (go) location.replace(go);                       // visit a real page, with the app's own security headers
var t0 = Date.now(), checks = [];
var xopen = XMLHttpRequest.prototype.open, xsend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function (m, u) { this._u = u; return xopen.apply(this, arguments); };
XMLHttpRequest.prototype.send = function () {
  if (String(this._u).indexOf("/submit/check") >= 0) checks.push(Date.now() - t0);
  return xsend.apply(this, arguments);
};
function $(s) { return document.querySelector(s); }
function setVal(sel, v) { var e = $(sel); e.value = v; e.dispatchEvent(new Event("input", {bubbles: true})); }
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function text(sel) { return ($(sel) ? $(sel).textContent : "").replace(/\\s+/g, " "); }
function day(n) { return new Date(Date.now() + n * 864e5).toISOString().slice(0, 10); }
function report(r) {
  var x = new XMLHttpRequest(); x.open("POST", "/__result"); x.send(JSON.stringify(r));
}
function fill(extra) {
  setVal("#field-title", "Live title"); setVal("#field-product", "loan"); setVal("#field-channel", "email");
  setVal("#field-launch_date", day(30)); setVal("#field-copy", extra || "Guaranteed approval, 5.99%.");
}
if (!go) fetch("/submit").then(function (r) { return r.text(); }).then(function (html) {
  var doc = new DOMParser().parseFromString(html, "text/html");
  $("#root").innerHTML = doc.querySelector(".submit-layout").outerHTML;
  var s = document.createElement("script");
  s.src = doc.querySelector("script[src*='submit.js']").getAttribute("src");
  s.onload = function () { htmx.process(document.body); run(); };
  document.body.appendChild(s);
});
async function run() {
  var r = {};
  await wait(300); checks.length = 0;
  if (scenario === "live") {
    setVal("#field-product", "loan"); setVal("#field-channel", "email"); await wait(1200); checks.length = 0;
    for (var i = 0; i < 6; i++) { setVal("#field-copy", "Guaranteed approval " + i); await wait(100); }
    r.beforePause = checks.length;                       // nothing sent while typing continues
    await wait(1500);
    r.sentAfterBurst = checks.length;
    r.panel = text("#precheck-body");
    r.indicatorVisible = getComputedStyle($("#precheck-status")).visibility;
    r.copyKept = $("#field-copy").value;
    checks.length = 0;
    setVal("#field-launch_date", day(1)); await wait(1200);
    r.rushWarning = text("#launch-warning");
    setVal("#field-launch_date", day(30)); await wait(1200);
    r.afterFar = text("#launch-warning");
    r.warningNodes = document.querySelectorAll("#launch-warning").length;
  } else if (scenario === "race") {
    setVal("#field-product", "loan"); setVal("#field-channel", "email"); await wait(1200); checks.length = 0;
    setVal("#field-copy", "Guaranteed approval first");     // this answer is held back by the server
    await wait(900);
    setVal("#field-copy", "Hello second");                  // this one answers at once
    await wait(4000);
    r.sent = checks.length;
    r.panel = text("#precheck-body");
  } else if (scenario === "double") {
    fill(); await wait(1200);
    var b = $(".submit-button"); b.click(); b.click(); r.disabled = b.disabled;
  } else if (scenario === "check") {
    fill(); await wait(1200);
    $(".check-flags").click();
  }
  if (scenario !== "double" && scenario !== "check") report(r);
}
</script></body></html>"""


class Tap:
    """ASGI wrapper: logs requests, delays chosen /submit/check answers, serves the harness."""

    def __init__(self, app):
        self.app = app
        self.log = []
        self.delays = []
        self.results = []
        self.got = threading.Event()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        if path == "/__harness":
            body = HARNESS.encode()
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", b"text/html; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                (b"content-security-policy", HARNESS_CSP.encode())]})
            return await send({"type": "http.response.body", "body": body})
        if path == "/__result":
            msg = await receive()
            self.results.append(json.loads(msg.get("body", b"{}") or b"{}"))
            self.got.set()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            return await send({"type": "http.response.body", "body": b""})
        self.log.append((method, path, headers.get("hx-request") == "true"))
        if path == "/submit/check" and self.delays:
            # Hold back the answer, not the reading of the request: this is a slow server, not a slow client.
            delay = self.delays.pop(0)
            held = send

            async def slow_send(message):
                if message["type"] == "http.response.start":
                    await asyncio.sleep(delay)
                try:
                    await held(message)
                except Exception:
                    pass  # the browser gave up on this request (that is the point of the test)
            return await self.app(scope, receive, slow_send)
        await self.app(scope, receive, send)

    def posts(self, path):
        return [e for e in self.log if e[0] == "POST" and e[1] == path]


class Live:
    def __init__(self, app):
        self.tap = Tap(app)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.tap, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def browse(self, path, wait_for_result=True, seconds=3.0, width=1366, height=900):
        """Open `path` in headless Chrome. Returns (console lines, harness result or None)."""
        self.tap.got.clear()
        with tempfile.TemporaryDirectory() as tmp:
            err = Path(tmp) / "err.txt"
            with open(err, "w") as errfile:
                proc = subprocess.Popen(
                    [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--enable-logging=stderr", "--v=0",
                     "--user-data-dir=%s" % tmp, "--window-size=%d,%d" % (width, height),
                     "http://127.0.0.1:%d%s" % (self.port, path)], stderr=errfile, stdout=subprocess.DEVNULL)
                try:
                    if wait_for_result:
                        self.tap.got.wait(30)
                    time.sleep(seconds if not wait_for_result else 0.5)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            lines = [l for l in err.read_text().splitlines() if "CONSOLE" in l]
        return lines, (self.tap.results[-1] if wait_for_result and self.tap.results else None)
