// busy-submit.js — a submit button that says it's working.
//
//   <button type="submit" data-busy-label="Thinking…">Ask</button>
//
// For forms whose answer takes a few seconds (the owner assistant). After
// the submit has been dispatched, the button is disabled (no double asks)
// and shows its busy label. The photo form handles its own (photo-upload.js).
// An enhancement: without it the form submits exactly the same.

(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form:not([data-photo-upload])").forEach(function (form) {
      var button = form.querySelector("button[data-busy-label]");
      if (!button) return;
      form.addEventListener("submit", function () {
        setTimeout(function () {
          button.disabled = true;
          button.textContent = button.getAttribute("data-busy-label");
        }, 0);
      });
    });
  });
})();
