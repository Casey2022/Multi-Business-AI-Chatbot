// theme.js — the switch between Night glass (the default) and Daylight.
//
// The choice is kept in this browser (localStorage "simon-theme") and put on
// <html> as data-theme="light"; app.css's Daylight tokens hang off that.
// The page's <head> applies a kept choice before it draws
// (templates/_theme_head.html); this shows the switch and runs it.
// Storage can be blocked (private windows): then the switch still works,
// for this page.

(function () {
  "use strict";
  var KEY = "simon-theme";
  var root = document.documentElement;

  function light() { return root.dataset.theme === "light"; }

  function label(button) {
    var on = light();
    button.textContent = on ? "Night" : "Light";
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.setAttribute("aria-label", on ? "Use the night theme" : "Use the light theme");
  }

  function set(toLight) {
    if (toLight) root.dataset.theme = "light"; else delete root.dataset.theme;
    try { localStorage.setItem(KEY, toLight ? "light" : "night"); }
    catch (e) { /* blocked: this page only */ }
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    label(button);
    button.hidden = false;
    button.addEventListener("click", function () {
      set(!light());
      document.querySelectorAll("[data-theme-toggle]").forEach(label);
    });
  });
})();
