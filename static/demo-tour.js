// demo-tour.js — a few pop-up tips for demo visitors, one at a time.
//
//   <div class="card-title" data-tour-step="5" data-tour-title="Everything it knows"
//        data-tour-text="Each section is…">
//
// A tip is declared on the element it points at (the ui.tip macro writes
// the attributes, and only for a demo), so its words live with the markup.
// The step number is the tip's name and its place in the order: a page
// shows its tips that haven't been seen yet, lowest first. Tips shared by
// several pages (the nav's "Chat as a customer") are seen once, wherever.
//
// Remembered in this browser: which tips were seen, and whether the visitor
// pressed "Skip tour". Storage can be blocked (private windows): then the
// tips simply show again on the next page. [data-tour-restart] buttons (in
// the demo bar) clear that and start again. They're hidden in the markup
// and shown here, so without JavaScript there's no button that does nothing.
//
// An enhancement: without it the pages are exactly the same.

(function () {
  "use strict";
  var KEY = "simon-tour";
  var GAP = 12;          // between the tip and what it points at
  var EDGE = 12;         // the least room left at the sides of the screen
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function load() {
    try {
      var kept = JSON.parse(localStorage.getItem(KEY) || "null");
      if (kept && Array.isArray(kept.seen)) return kept;
    } catch (e) { /* blocked or unreadable */ }
    return { seen: [], off: false };
  }
  function save(state) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); }
    catch (e) { /* storage blocked: the tips just come back next page */ }
  }

  // Tips on this page, in order: ones that are shown (an element inside a
  // closed <details> or display:none has no box to point at) and have words.
  function stepsHere() {
    var found = Array.prototype.slice.call(document.querySelectorAll("[data-tour-step]"));
    return found.filter(function (el) {
      return el.getAttribute("data-tour-text") && el.getClientRects().length;
    }).sort(function (a, b) {
      return Number(a.getAttribute("data-tour-step")) - Number(b.getAttribute("data-tour-step"));
    });
  }

  var tip, titleEl, textEl, countEl, nextBtn, skipBtn, arrow;
  var queue = [], at = 0, target = null, returnFocus = null;

  function build() {
    tip = document.createElement("div");
    tip.className = "tour-tip";
    tip.setAttribute("role", "dialog");
    tip.setAttribute("aria-labelledby", "tour-title");
    tip.setAttribute("aria-describedby", "tour-text");
    tip.tabIndex = -1;

    arrow = document.createElement("div");
    arrow.className = "tour-arrow";
    countEl = document.createElement("div");
    countEl.className = "tour-count";
    titleEl = document.createElement("div");
    titleEl.className = "tour-title";
    titleEl.id = "tour-title";
    textEl = document.createElement("p");
    textEl.className = "tour-text";
    textEl.id = "tour-text";
    textEl.setAttribute("aria-live", "polite");

    var actions = document.createElement("div");
    actions.className = "tour-actions";
    skipBtn = document.createElement("button");
    skipBtn.type = "button";
    skipBtn.className = "btn btn-sm btn-quiet";
    skipBtn.textContent = "Skip tour";
    nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "btn btn-sm btn-primary";
    actions.appendChild(skipBtn);
    actions.appendChild(nextBtn);

    [arrow, countEl, titleEl, textEl, actions].forEach(function (el) { tip.appendChild(el); });
    skipBtn.addEventListener("click", function () { finish(true); });
    nextBtn.addEventListener("click", next);
    tip.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); finish(true); }
    });
    document.body.appendChild(tip);
  }

  function markSeen(el) {
    var state = load();
    var step = el.getAttribute("data-tour-step");
    if (state.seen.indexOf(step) === -1) state.seen.push(step);
    save(state);
  }

  function show() {
    if (target) target.classList.remove("tour-target");
    target = queue[at];
    target.classList.add("tour-target");
    titleEl.textContent = target.getAttribute("data-tour-title") || "";
    titleEl.hidden = !titleEl.textContent;
    textEl.textContent = target.getAttribute("data-tour-text");
    countEl.textContent = queue.length > 1 ? "Tip " + (at + 1) + " of " + queue.length : "Tip";
    nextBtn.textContent = at === queue.length - 1 ? "Done" : "Next";

    tip.classList.remove("is-shown");
    tip.hidden = false;
    place(true);
    // Laid out once at rest before it's shown, so the fade runs. (Not
    // requestAnimationFrame: a tab in the background never gets a frame,
    // and the tip would stay invisible.)
    void tip.offsetWidth;
    tip.classList.add("is-shown");
    nextBtn.focus({ preventScroll: true });
  }

  // Below what it points at when there's room, else above; slid sideways
  // to stay on screen, with the arrow still under the target's middle.
  // Positioned in page coordinates, so scrolling carries it along. A target
  // taller than a third of the screen (a whole knowledge section on a
  // phone is 900px) is pointed at by its top part, or the tip would sit
  // below the bottom of the screen.
  function place(scroll) {
    if (!target) return;
    var vw = document.documentElement.clientWidth;
    var vh = window.innerHeight;
    var r = target.getBoundingClientRect();
    var sx = window.pageXOffset, sy = window.pageYOffset;
    var w = Math.min(320, vw - 2 * EDGE);
    tip.style.width = w + "px";
    var h = tip.offsetHeight;

    var top = r.top + sy;
    var tall = Math.min(r.height, Math.max(48, vh / 3));
    var below = top + tall + GAP;
    var above = top - GAP - h;
    var docBottom = document.documentElement.scrollHeight;
    var under = below + h <= docBottom || above < 0;
    var y = under ? below : above;

    var middle = r.left + sx + r.width / 2;
    var x = Math.max(sx + EDGE, Math.min(middle - w / 2, sx + vw - EDGE - w));
    tip.style.left = x + "px";
    tip.style.top = y + "px";
    tip.classList.toggle("is-above", !under);
    arrow.style.left = Math.max(16, Math.min(middle - x, w - 16)) + "px";

    if (scroll) {
      // Bring the target and its tip into view together, centred if they
      // fit; a target taller than the screen keeps its top in view.
      var from = Math.min(top, y), to = Math.max(top + tall, y + h);
      var want = from - Math.max(EDGE, (vh - (to - from)) / 2);
      if (from < sy + EDGE || to > sy + vh - EDGE) {
        window.scrollTo({ top: Math.max(0, want), behavior: calm ? "auto" : "smooth" });
      }
    }
  }

  function next() {
    markSeen(target);
    if (at < queue.length - 1) { at += 1; show(); }
    else finish(false);
  }

  function finish(skipped) {
    if (skipped) {
      var state = load();
      state.off = true;
      save(state);
    } else {
      markSeen(target);
    }
    if (target) target.classList.remove("tour-target");
    target = null;
    tip.hidden = true;
    tip.classList.remove("is-shown");
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus({ preventScroll: true });
  }

  function start(all) {
    var state = load();
    if (state.off && !all) return;
    queue = stepsHere().filter(function (el) {
      return all || state.seen.indexOf(el.getAttribute("data-tour-step")) === -1;
    });
    if (!queue.length) return;
    if (!tip) build();
    returnFocus = tip.contains(document.activeElement) ? returnFocus : document.activeElement;
    at = 0;
    show();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-tour-restart]").forEach(function (button) {
      button.hidden = false;
      button.addEventListener("click", function () {
        save({ seen: [], off: false });
        start(true);
      });
    });
    window.addEventListener("resize", function () { place(false); });
    // The portal's blocks fade up as the page loads (app.css), moving as
    // they go; measured mid-way, a tip points at where a card was. So wait
    // for those to end. Endless ones (a pulsing dot) never would, and a
    // tab in the background holds them still, so a second is the most we wait.
    var running = document.getAnimations ? document.getAnimations().filter(function (a) {
      return a.effect && a.effect.getTiming().iterations !== Infinity;
    }) : [];
    var begun = false;
    function begin() { if (!begun) { begun = true; start(false); } }
    Promise.all(running.map(function (a) { return a.finished.catch(function () {}); }))
      .then(function () { if (begun) place(false); else begin(); });
    setTimeout(begin, 1000);
  });
})();
