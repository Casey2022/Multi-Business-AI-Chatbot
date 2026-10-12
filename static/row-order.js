// row-order.js — ↑ / ↓ buttons that move a table row, for the menu editor.
//
//   <tbody data-reorder>
//     <tr> <td><input data-order …> <button data-move="up" hidden> … </td> … </tr>
//
// The order lives in the numbered inputs ("Order"), which the server sorts
// by: this only swaps rows and renumbers them 1, 2, 3…, then fires an input
// event so the card's Save lights up. Without the script the buttons stay
// hidden and the owner types the numbers.

(function () {
  "use strict";

  function renumber(body) {
    var inputs = body.querySelectorAll("input[data-order]");
    Array.prototype.forEach.call(inputs, function (input, i) {
      input.value = String(i + 1);
    });
    var form = body.closest("form");
    if (form) form.dispatchEvent(new Event("input", { bubbles: true }));
  }

  document.addEventListener("DOMContentLoaded", function () {
    var bodies = document.querySelectorAll("tbody[data-reorder]");
    Array.prototype.forEach.call(bodies, function (body) {
      Array.prototype.forEach.call(body.querySelectorAll("[data-move]"), function (b) {
        b.hidden = false;
      });
      body.addEventListener("click", function (e) {
        var button = e.target.closest("[data-move]");
        if (!button) return;
        var row = button.closest("tr");
        if (button.getAttribute("data-move") === "up" && row.previousElementSibling) {
          body.insertBefore(row, row.previousElementSibling);
        } else if (button.getAttribute("data-move") === "down" && row.nextElementSibling) {
          body.insertBefore(row.nextElementSibling, row);
        } else {
          return;
        }
        renumber(body);
        button.focus();
      });
    });
  });
})();
