// Submit form. Progressive enhancement only: the server already validates every field and refuses
// a duplicate, so the form works (and stays safe) without this script. It adds a live character
// count, and one submit disables the buttons so a double click sends one request.
(function () {
  "use strict";
  var form = document.querySelector("form.submit-form");
  if (!form) return;
  var buttons = Array.prototype.slice.call(form.querySelectorAll("button[type=submit]"));
  var copy = form.querySelector("textarea[name=copy]");
  var count = document.getElementById("copy-count");
  var sent = false;

  function format(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  if (copy && count) {
    var max = format(copy.getAttribute("maxlength") || "10000");
    var update = function () {
      // Count characters the way the server does: code points, not UTF-16 units.
      count.textContent = format(Array.from(copy.value).length) + " / " + max + " characters";
    };
    copy.addEventListener("input", update);
    update();
  }

  function unlock() {
    sent = false;
    form.removeAttribute("aria-busy");
    buttons.forEach(function (b) { b.disabled = false; });
  }

  form.addEventListener("submit", function (event) {
    if (sent) { event.preventDefault(); return; }
    sent = true;
    form.setAttribute("aria-busy", "true");
    // A disabled submit button is left out of the data; neither of ours carries a value.
    buttons.forEach(function (b) { b.disabled = true; });
  });

  // Coming back with Back/Forward can restore the page from the browser's cache with the buttons
  // still disabled.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) unlock();
  });
})();
