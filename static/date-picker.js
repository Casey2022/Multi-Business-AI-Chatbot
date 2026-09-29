// date-picker.js — "Move this appointment" opens a calendar, not a form.
//
// The browser's own date-and-time field shows "mm/dd/yyyy, --:-- --" boxes
// with a small calendar icon beside them; owners reached for the icon every
// time (Casey, 2026-09-28). So the boxes are hidden and one button opens the
// browser's calendar picker directly; the chosen time is then spelled out
// ("Moves to Wednesday, September 30 at 2:00 PM") before anything is saved.
//
// Mark the form with data-move-picker, and inside it the field
// (data-picker-input), the button that opens it (data-picker-open, rendered
// hidden), where the choice is spelled out (data-picker-choice) and the
// submit button (data-picker-submit).
//
// An enhancement, not the mechanism: without this script, or in a browser
// that can't open the picker from a button (no showPicker), the page keeps
// the plain field and works exactly as before. The server still decides what
// the submitted value means.

(function () {
  "use strict";

  var FORMAT = { weekday: "long", month: "long", day: "numeric",
                 hour: "numeric", minute: "2-digit" };

  function spelled(value) {
    // "2026-09-30T14:00" has no zone, so Date reads it as local time and
    // prints it back unchanged: the business-local time the server stores.
    var when = new Date(value);
    return isNaN(when) ? value : when.toLocaleString(undefined, FORMAT)
      .replace(/, (\d{1,2}:\d\d)/, " at $1");
  }

  function setUp(form) {
    var input = form.querySelector("[data-picker-input]");
    var open = form.querySelector("[data-picker-open]");
    var choice = form.querySelector("[data-picker-choice]");
    var submit = form.querySelector("[data-picker-submit]");
    if (!input || !open || typeof input.showPicker !== "function") return;

    var original = input.value;
    input.classList.add("picker-hidden");
    input.tabIndex = -1;
    open.hidden = false;
    if (submit) submit.disabled = true;

    open.addEventListener("click", function () {
      try {
        input.showPicker();
      } catch (e) {
        // Refused (rare): fall back to the plain field.
        input.classList.remove("picker-hidden");
        input.tabIndex = 0;
        open.hidden = true;
        if (submit) submit.disabled = false;
        input.focus();
      }
    });

    input.addEventListener("change", function () {
      var changed = input.value && input.value !== original;
      if (choice) choice.textContent = changed ? "Moves to " + spelled(input.value) : "";
      if (submit) submit.disabled = !changed;
    });
  }

  document.querySelectorAll("[data-move-picker]").forEach(setUp);
})();
