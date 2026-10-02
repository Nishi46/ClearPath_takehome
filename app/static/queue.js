// Whole-row click for the queue. Progressive enhancement: the title is already a real link, so
// keyboard users and anyone without JavaScript lose nothing. A plain click anywhere else in a
// row follows that link. Clicks on other controls, modified clicks (open in a new tab) and
// clicks that end a text selection are left alone.
(function () {
  "use strict";
  var table = document.querySelector("table.queue");
  if (!table) return;
  table.classList.add("row-clickable");

  table.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var target = event.target;
    if (!target.closest) return;
    if (target.closest("a, button, input, select, textarea, label")) return;
    var row = target.closest("tbody tr");
    if (!row) return;
    var selection = window.getSelection && window.getSelection();
    if (selection && String(selection).length > 0) return;
    var link = row.querySelector("a[href]");
    if (link) link.click();
  });
})();
