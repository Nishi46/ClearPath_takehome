// Decision form for the review screen. Progressive enhancement only: the server already refuses a
// blank reason and a second decision, so the form works (and stays safe) without this script.
// It adds two conveniences: Request changes and Reject stay disabled until the reason has
// visible text, and one submit disables the buttons so a double click sends one request.
(function () {
  "use strict";
  var form = document.querySelector("form.decision-form");
  if (!form) return;
  var reason = form.querySelector("textarea[name=reason]");
  var buttons = Array.prototype.slice.call(form.querySelectorAll("button[name=outcome]"));
  var needReason = buttons.filter(function (b) {
    return b.value === "changes_requested" || b.value === "rejected";
  });
  if (!reason || !buttons.length) return;

  var sent = false;
  var carried = null;

  // Spaces, tabs, newlines and zero-width characters do not count as text (the server agrees).
  function hasText() {
    return /[^\s​-‍⁠﻿]/.test(reason.value);
  }

  function sync() {
    var ok = hasText();
    needReason.forEach(function (b) { b.disabled = !ok; });
  }

  function unlock() {
    sent = false;
    if (carried && carried.parentNode) carried.parentNode.removeChild(carried);
    carried = null;
    form.removeAttribute("aria-busy");
    buttons.forEach(function (b) { b.disabled = false; });
    sync();
  }

  reason.addEventListener("input", sync);

  form.addEventListener("submit", function (event) {
    if (sent) { event.preventDefault(); return; }
    var chosen = event.submitter && event.submitter.name === "outcome" ? event.submitter.value : null;
    if (!chosen) return; // not sent by one of our buttons: leave it to the server
    // A disabled button is left out of the submitted data, so carry the choice in a hidden field
    // before disabling.
    carried = document.createElement("input");
    carried.type = "hidden";
    carried.name = "outcome";
    carried.value = chosen;
    form.appendChild(carried);
    sent = true;
    form.setAttribute("aria-busy", "true");
    buttons.forEach(function (b) { b.disabled = true; });
  });

  // Coming back with Back/Forward can restore the page from the browser's cache with the buttons
  // still disabled.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) unlock();
  });

  sync();
})();
