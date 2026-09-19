// field-deps.js — a control that only means something once another control
// says so.
//
// Mark the dependent field with the name of the field it follows and the
// answer that makes it live:
//
//   <select name="business.service_radius.miles"
//           data-enabled-by="business.service_radius.enabled"
//           data-enabled-when="yes">
//
// Why data attributes rather than a line of code per pair: the rule lives
// next to the field it governs, in the template, where someone editing that
// field will see it. A list of pairs kept in here is a second place to
// remember, and the one that gets forgotten.
//
// This is an enhancement, not the mechanism. The server renders the field
// disabled when it should be, so the page is already correct before any
// script runs, and the server decides what a submitted value means either
// way — a distance is ignored when the limit is off, whatever the browser
// allowed. All this does is stop an owner picking a number that was never
// going to apply, and show them why it's greyed.

(function () {
  "use strict";

  function sync(field) {
    var controllerName = field.getAttribute("data-enabled-by");
    var wanted = field.getAttribute("data-enabled-when");
    var controller = document.querySelector('[name="' + controllerName + '"]');
    if (!controller) return;

    // A disabled controller means the whole group is switched off for a
    // reason this script doesn't know about — leave it exactly as the
    // server rendered it rather than overriding a decision made upstream.
    if (controller.disabled) return;

    var live = controller.value === wanted;
    field.disabled = !live;

    var wrapper = field.closest(".field");
    if (wrapper) wrapper.classList.toggle("is-inert", !live);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var fields = document.querySelectorAll("[data-enabled-by]");
    Array.prototype.forEach.call(fields, function (field) {
      var controller = document.querySelector(
        '[name="' + field.getAttribute("data-enabled-by") + '"]');
      if (!controller) return;
      controller.addEventListener("change", function () { sync(field); });
      sync(field);
    });
  });
})();
