// rule-cards.js — the cancellation rules page (cancellation.html): one card
// per service, opened the moment its box is ticked.
//
// Casey, 2026-10-17: "having to tick the box and then hit save so the
// service loads … is a little confusing." The server renders a card for
// every service, hidden unless ticked, already filled with a copy of the
// rules for everything else; this shows it on the tick. The first time a
// new card opens it copies the "everything else" card as it is NOW (the
// owner may have changed it since the page loaded), so a service starts
// the same as everything else and only what should differ is changed.
//
// An enhancement, not the mechanism: without it, ticking and saving opens
// the card (the server ignores a hidden card's fields unless its box is
// ticked), and every window row shows instead of the "Add a window" button.

(function () {
  "use strict";

  var each = function (list, fn) { Array.prototype.forEach.call(list, fn); };

  function windows(card) { return card.querySelectorAll("[data-window]"); }

  function amount(row) {
    var box = row.querySelector('input[name$="_amount"]');
    return box ? box.value.trim() : "";
  }

  // Show the "Add a window" button while there's a row left to add.
  function syncAdd(card) {
    var button = card.querySelector("[data-add-window]");
    if (!button) return;
    button.hidden = !Array.prototype.some.call(windows(card), function (r) { return r.hidden; });
  }

  // Empty rows wait behind "Add a window" (the first row always shows).
  function tidy(card) {
    each(windows(card), function (row, i) { row.hidden = i > 0 && !amount(row); });
    syncAdd(card);
  }

  function changed(field) {
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Copy one card's values into another, field by field (gen_w0_type →
  // k_colour_w0_type), then let field-deps.js re-check the percent boxes.
  function copy(from, to) {
    var a = from.getAttribute("data-card-key") + "_";
    var b = to.getAttribute("data-card-key") + "_";
    each(from.querySelectorAll("[name]"), function (field) {
      if (field.name.indexOf(a) !== 0) return;
      var twin = to.querySelector('[name="' + b + field.name.slice(a.length) + '"]');
      if (twin) twin.value = field.value;
    });
    each(to.querySelectorAll("select"), changed);
    tidy(to);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("[data-rule-cards]");
    if (!form) return;
    var general = form.querySelector('[data-card-key="gen"]');

    function syncGeneral() {
      var any = form.querySelector("[data-service-tick]:checked") !== null;
      var title = form.querySelector("[data-title-alone]");
      if (title) title.textContent = title.getAttribute(any ? "data-title-others" : "data-title-alone");
      var note = form.querySelector("[data-shows-with-services]");
      if (note) note.hidden = !any;
    }

    each(form.querySelectorAll("[data-card-key]"), function (card) {
      tidy(card);
      var add = card.querySelector("[data-add-window]");
      if (add) add.addEventListener("click", function () {
        var next = Array.prototype.find.call(windows(card), function (r) { return r.hidden; });
        if (!next) return;
        next.hidden = false;
        next.querySelector('input[name$="_amount"]').focus();
        syncAdd(card);
      });
      each(card.querySelectorAll("[data-clear-window]"), function (button) {
        button.hidden = false;
        button.addEventListener("click", function () {
          var row = button.closest("[data-window]");
          row.querySelector('input[name$="_amount"]').value = "";
          row.hidden = true;
          syncAdd(card);
          changed(row.querySelector('input[name$="_amount"]'));
        });
      });
    });

    each(form.querySelectorAll("[data-service-tick]"), function (tick) {
      var card = document.getElementById("card-" + tick.getAttribute("data-service-tick"));
      if (!card) return;
      tick.addEventListener("change", function () {
        if (tick.checked) {
          if (card.hasAttribute("data-fresh") && general) {
            copy(general, card);
            card.removeAttribute("data-fresh");
          }
          card.hidden = false;
          card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } else {
          card.hidden = true;
        }
        syncGeneral();
      });
      var remove = card.querySelector("[data-remove-card]");
      if (remove) {
        remove.hidden = false;
        remove.addEventListener("click", function () {
          tick.checked = false;
          changed(tick);
          tick.focus();
        });
      }
    });
    syncGeneral();
  });
})();
