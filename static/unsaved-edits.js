// unsaved-edits.js — a knowledge section's Save only lights up once there's
// something to save, and leaving the page with unsaved typing asks first.
//
// Mark the form, and its submit button:
//
//   <form ... data-track-edits>
//     <span data-unsaved-flag hidden>Unsaved edits</span>   (optional)
//     <input name="title"> <textarea name="body"></textarea>
//     <button type="submit" data-save-button>Save</button>
//
// "Something to save" means the title or body differs from what the server
// rendered (the field's defaultValue) AND neither is blank, since the server
// refuses a blank section. Typing a section back to its saved wording greys
// Save again. For an empty "Add a section" form that's simply: both filled.
// Compared the way the server compares: line endings and surrounding
// whitespace don't count.
//
// An enhancement, not the mechanism: without this script every button
// works, the server still refuses blank sections, and a save that changes
// nothing is reported as "Nothing changed" rather than filed.

(function () {
  "use strict";

  function norm(text) {
    return (text || "").replace(/\r\n?/g, "\n").trim();
  }

  function fields(form) {
    return Array.prototype.slice.call(
      form.querySelectorAll("input[name='title'], textarea[name='body']"));
  }

  function isEdited(form) {
    return fields(form).some(function (el) {
      return norm(el.value) !== norm(el.defaultValue);
    });
  }

  function isComplete(form) {
    var list = fields(form);
    return list.length > 0 && list.every(function (el) {
      return norm(el.value) !== "";
    });
  }

  function sync(form) {
    var edited = isEdited(form);
    var button = form.querySelector("[data-save-button]");
    if (button) {
      var ready = edited && isComplete(form);
      button.disabled = !ready;
      button.title = ready ? "" :
        (edited ? "A section needs both a title and content."
                : "Nothing to save yet — edit the title or text first.");
    }
    var flag = form.querySelector("[data-unsaved-flag]");
    if (flag) flag.hidden = !edited;
  }

  // The form being submitted is about to be saved, so it doesn't count as
  // unsaved — but every OTHER edited form on the page would be lost by the
  // navigation, so those still warn.
  var submitting = null;

  document.addEventListener("DOMContentLoaded", function () {
    var forms = document.querySelectorAll("form[data-track-edits]");
    Array.prototype.forEach.call(forms, function (form) {
      form.addEventListener("input", function () { sync(form); });
      form.addEventListener("submit", function () { submitting = form; });
      sync(form);
    });

    // Any other form (Preview, Publish, Discard, Delete) leaves the page too.
    document.addEventListener("submit", function (e) {
      if (!e.target.hasAttribute("data-track-edits")) submitting = null;
    }, true);

    window.addEventListener("beforeunload", function (e) {
      var unsaved = Array.prototype.some.call(forms, function (form) {
        return form !== submitting && isEdited(form);
      });
      if (!unsaved) return;
      // Browsers show their own wording; setting returnValue is what asks.
      e.preventDefault();
      e.returnValue = "";
    });

    // A Back/Forward restore brings the page back with the typed values
    // still in the fields; re-check so the buttons match what's there.
    window.addEventListener("pageshow", function () {
      submitting = null;
      Array.prototype.forEach.call(forms, sync);
    });
  });
})();
