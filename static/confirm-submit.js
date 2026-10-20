// confirm-submit.js — "are you sure?" before a form that can't be undone.
//
//   <form method="POST" data-confirm="Delete this section?">
//
// Replaces onsubmit="return confirm('…')", which the Content-Security-Policy
// (admin/headers.py) now refuses to run, and which was unsafe: a menu item's
// name went inside the JavaScript string, where HTML escaping doesn't
// protect (2026-10-20). Read from an attribute, the text is only ever text.
//
// Listens on the document in the capture phase, so a cancelled submit stops
// before any other script (unsaved-edits.js, busy-submit.js) sees it.
// An enhancement: without JavaScript the form submits without asking, as the
// inline version did.

(function () {
  "use strict";
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form && form.getAttribute && form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
})();
