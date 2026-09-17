/* table-resize.js — drag a column edge to widen or narrow it.
 *
 * Progressive enhancement: the tables work without this file. It finds any
 * <table data-resizable>, puts a grip on each header edge, and remembers
 * what the owner set.
 *
 * Two things make it behave rather than fight the page:
 *
 *  - Widths are only applied once the owner has actually dragged something.
 *    Before that the browser sizes the columns from the content, which is
 *    usually right. Switching to table-layout:fixed on load would freeze
 *    a set of widths nobody chose.
 *
 *  - It switches itself off under 640px, where the stylesheet stacks each
 *    row into a record and there are no columns left to resize.
 */
(function () {
  "use strict";

  var MIN = 60;                       // px — narrower than this is unreadable
  var PHONE = window.matchMedia("(max-width: 640px)");

  function storageKey(table) {
    return "colwidths:" + (table.dataset.resizable || location.pathname);
  }

  function remember(table, widths) {
    // Wrapped because storage throws in a private window and can simply be
    // absent. A forgotten column width is not worth an exception.
    try { localStorage.setItem(storageKey(table), JSON.stringify(widths)); }
    catch (e) { /* the page works fine without it */ }
  }

  function recall(table) {
    try {
      var saved = localStorage.getItem(storageKey(table));
      return saved ? JSON.parse(saved) : null;
    } catch (e) { return null; }
  }

  function applyWidths(table, widths) {
    if (!widths) return;
    var headers = table.tHead.rows[0].cells;
    table.style.tableLayout = "fixed";
    for (var i = 0; i < headers.length && i < widths.length; i++) {
      if (widths[i]) headers[i].style.width = widths[i] + "px";
    }
  }

  function currentWidths(table) {
    return Array.prototype.map.call(
      table.tHead.rows[0].cells,
      function (th) { return Math.round(th.getBoundingClientRect().width); }
    );
  }

  function setUp(table) {
    if (!table.tHead || !table.tHead.rows.length) return;
    applyWidths(table, recall(table));

    Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, index) {
      // The last column has no edge to drag: widening it would just push
      // the table wider with nothing to give the space back.
      if (index === table.tHead.rows[0].cells.length - 1) return;

      var grip = document.createElement("span");
      grip.className = "col-grip";
      grip.setAttribute("aria-hidden", "true");
      th.appendChild(grip);

      grip.addEventListener("mousedown", function (event) {
        if (PHONE.matches) return;
        event.preventDefault();

        // Freeze every column at its current width first, so dragging one
        // edge moves that edge instead of reflowing the whole table.
        var widths = currentWidths(table);
        applyWidths(table, widths);

        var startX = event.clientX;
        var startWidth = widths[index];
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";

        function onMove(moveEvent) {
          var next = Math.max(MIN, startWidth + (moveEvent.clientX - startX));
          th.style.width = next + "px";
        }

        function onUp() {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          remember(table, currentWidths(table));
        }

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });

      // Double-clicking an edge hands the column back to the browser.
      grip.addEventListener("dblclick", function () {
        th.style.width = "";
        remember(table, currentWidths(table));
      });
    });
  }

  function start() {
    Array.prototype.forEach.call(
      document.querySelectorAll("table[data-resizable]"), setUp
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
