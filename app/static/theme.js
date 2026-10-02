// Light/dark toggle. Loaded in <head> without defer so the saved choice is applied before the page
// paints (no flash). With no saved choice the system setting decides (see style.css). The button is
// hidden in the markup and only shown here, because without JavaScript it could do nothing.
(function () {
  "use strict";
  var KEY = "clearpath-theme";
  var root = document.documentElement;

  function saved() {
    try { var v = window.localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : null; } catch (e) { return null; }
  }
  function systemDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  function effective() { return root.getAttribute("data-theme") || (systemDark() ? "dark" : "light"); }

  var choice = saved();
  if (choice) root.setAttribute("data-theme", choice);

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    function sync() { button.setAttribute("aria-pressed", effective() === "dark" ? "true" : "false"); }
    button.hidden = false;
    sync();
    button.addEventListener("click", function () {
      var next = effective() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { window.localStorage.setItem(KEY, next); } catch (e) { /* the choice just lasts for this page */ }
      sync();
    });
  });
})();
