// assistant-pop.js — the owner assistant in a panel on every owner page.
//
//   <a data-assistant-toggle href="/admin/help#assistant">  the corner button
//   <section id="assistant-pop" hidden>                     the panel
//     [data-assistant-thread] [data-assistant-form] [data-assistant-clear]
//
// An enhancement: without this script the button is a link to the Help
// page, where the same assistant works with ordinary form posts. With it,
// the button opens the panel, questions go by fetch (the server answers in
// JSON when X-Requested-With is "fetch"), and the panel remembers being open
// as the owner moves from page to page.
//
// When the assistant changes the menu, the page underneath is out of date,
// so it's refreshed (panel kept open) unless the owner has unsaved typing
// on it (unsaved-edits.js says), in which case they're told instead.

(function () {
  "use strict";

  var KEY = "assistant-pop-open";

  function remember(open) {
    try { open ? sessionStorage.setItem(KEY, "1") : sessionStorage.removeItem(KEY); }
    catch (e) { /* private mode: the panel just starts closed */ }
  }

  function remembered() {
    try { return sessionStorage.getItem(KEY) === "1"; } catch (e) { return false; }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;   // as text, not markup: a reply is never parsed as HTML
    return node;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var fab = document.querySelector("[data-assistant-toggle]");
    var pop = document.getElementById("assistant-pop");
    if (!fab || !pop) return;

    var thread = pop.querySelector("[data-assistant-thread]");
    var form = pop.querySelector("[data-assistant-form]");
    var input = form.querySelector("input[name='question']");
    var button = form.querySelector("button[type='submit']");
    var clear = pop.querySelector("[data-assistant-clear]");
    var empty = pop.querySelector(".assistant-pop-empty");

    function toBottom() { thread.scrollTop = thread.scrollHeight; }

    function open(focus) {
      pop.hidden = false;
      fab.setAttribute("aria-expanded", "true");
      fab.classList.add("is-open");
      remember(true);
      toBottom();
      if (focus) input.focus();
    }

    function close() {
      pop.hidden = true;
      fab.setAttribute("aria-expanded", "false");
      fab.classList.remove("is-open");
      remember(false);
      fab.focus();
    }

    fab.addEventListener("click", function (e) {
      e.preventDefault();
      if (pop.hidden) open(true); else close();
    });
    pop.querySelector("[data-assistant-close]").addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !pop.hidden) close();
    });
    if (remembered()) open(false);

    function addTurn(role, text) {
      var turn = el("div", "assistant-turn assistant-turn-" + role);
      turn.appendChild(el("div", "assistant-who", role === "owner" ? "You" : "Assistant"));
      turn.appendChild(el("div", "assistant-text", text));
      thread.appendChild(turn);
      if (empty) empty.hidden = true;
      toBottom();
      return turn;
    }

    function note(turn, cls, text, link) {
      var box = el("div", cls, text);
      if (link) {
        box.appendChild(document.createTextNode(" "));
        var a = el("a", null, link.text);
        a.href = link.href;
        box.appendChild(a);
      }
      turn.appendChild(box);
      toBottom();
      return box;
    }

    function busy(on) {
      button.disabled = on;
      input.disabled = on;
      button.textContent = on ? "…" : "Ask";
    }

    function send(url, body) {
      return fetch(url, {
        method: "POST", body: body, credentials: "same-origin",
        headers: { "X-Requested-With": "fetch", "Accept": "application/json" }
      });
    }

    form.addEventListener("submit", function (e) {
      var question = input.value.trim();
      if (!question) return;              // the browser's "required" says so
      e.preventDefault();
      var body = new FormData(form);
      addTurn("owner", question);
      input.value = "";
      var thinking = addTurn("assistant", "Thinking…");
      thinking.classList.add("is-thinking");
      busy(true);

      send(form.action, body)
        .then(function (r) {
          return r.json().catch(function () {
            return { error: "Something went wrong. Try again, or ask a person." };
          });
        })
        .then(function (res) {
          thinking.remove();
          if (res.error) {
            addTurn("assistant", res.error).classList.add("is-error");
            return;
          }
          var turn = addTurn("assistant", res.reply);
          if (res.actions && res.actions.length) {
            var box = el("div", "assistant-actions");
            box.appendChild(el("div", "assistant-actions-title", "Saved as a draft"));
            var list = el("ul");
            res.actions.forEach(function (line) { list.appendChild(el("li", null, line)); });
            box.appendChild(list);
            box.appendChild(el("div", null, "Customers see it once you press “Publish changes”."));
            turn.appendChild(box);
          }
          if (res.handoff) {
            note(turn, "assistant-handoff", "I've drafted a message to support from this conversation.",
                 { href: res.help_url, text: "Open it on the Help page" });
          }
          if (res.actions && res.actions.length) {
            var unsaved = typeof window.portalHasUnsavedEdits === "function" &&
                          window.portalHasUnsavedEdits();
            if (unsaved) {
              note(turn, "assistant-refresh", "This page has unsaved edits, so it wasn't " +
                   "refreshed. Save or discard them, then reload to see the change.");
            } else {
              note(turn, "assistant-refresh", "Refreshing the page to show it…");
              remember(true);
              // A GET of this address, never a re-post of a form.
              setTimeout(function () {
                window.location.assign(window.location.pathname + window.location.search);
              }, 1500);
            }
          }
        })
        .catch(function () {
          thinking.remove();
          addTurn("assistant", "Couldn't reach the server. Check your connection and try again.")
            .classList.add("is-error");
        })
        .then(function () { busy(false); input.focus(); });
    });

    clear.addEventListener("submit", function (e) {
      e.preventDefault();
      send(clear.action, new FormData(clear)).then(function (r) {
        if (!r.ok) return;
        Array.prototype.forEach.call(thread.querySelectorAll(".assistant-turn"),
                                     function (n) { n.remove(); });
        if (empty) empty.hidden = false;
        input.focus();
      });
    });
  });
})();
