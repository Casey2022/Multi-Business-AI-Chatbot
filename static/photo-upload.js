// photo-upload.js — shrink a phone photo in the browser before it's sent,
// and show that the upload is working.
//
//   <form data-photo-upload> <input type="file" name="photo">
//     <span data-photo-status></span>
//     <button type="submit" data-busy-label="Reading the photo…">
//
// A phone photo is often 3–8MB and 4000px wide; the model reads text fine
// at 2000px, the server refuses over 5MB, and a smaller file uploads faster
// on a phone connection. So anything over MAX_BYTES or MAX_SIDE is redrawn
// to fit MAX_SIDE and re-encoded as JPEG.
//
// An enhancement: without it (or where the browser can't decode the file,
// e.g. HEIC outside Safari) the original is sent, and the server explains
// what's wrong with it if anything is.

(function () {
  "use strict";

  var MAX_SIDE = 2000;
  var MAX_BYTES = 1.5 * 1024 * 1024;
  var QUALITY = 0.85;

  function say(form, text) {
    var status = form.querySelector("[data-photo-status]");
    if (status) status.textContent = text;
  }

  function mb(bytes) { return (bytes / 1024 / 1024).toFixed(1) + "MB"; }

  function shrink(file, done) {
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      var scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
      if (scale === 1 && file.size <= MAX_BYTES) return done(null);
      var canvas = document.createElement("canvas");
      canvas.width = Math.round(img.naturalWidth * scale);
      canvas.height = Math.round(img.naturalHeight * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (blob) { done(blob); }, "image/jpeg", QUALITY);
    };
    img.onerror = function () { URL.revokeObjectURL(url); done(null); };
    img.src = url;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var forms = document.querySelectorAll("form[data-photo-upload]");
    Array.prototype.forEach.call(forms, function (form) {
      var input = form.querySelector("input[type=file]");
      var button = form.querySelector("button[type=submit]");
      if (!input) return;

      input.addEventListener("change", function () {
        var file = input.files && input.files[0];
        say(form, "");
        if (!file) return;
        if (/hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(file.name)) {
          say(form, "This is an iPhone HEIC photo. If it can't be read, share it as a JPEG or take a screenshot.");
        }
        if (file.size <= MAX_BYTES && !/^image\//.test(file.type)) return;
        shrink(file, function (blob) {
          if (!blob || typeof DataTransfer === "undefined") return;
          try {
            var smaller = new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg",
                                   { type: "image/jpeg" });
            var dt = new DataTransfer();
            dt.items.add(smaller);
            input.files = dt.files;
            say(form, "Resized for upload (" + mb(file.size) + " → " + mb(smaller.size) + ").");
          } catch (e) { /* keep the original */ }
        });
      });

      form.addEventListener("submit", function () {
        if (!button) return;
        // After the submit has been dispatched, so the button's own
        // disabled state can't cancel it.
        setTimeout(function () {
          button.disabled = true;
          if (button.getAttribute("data-busy-label")) {
            button.textContent = button.getAttribute("data-busy-label");
          }
        }, 0);
        say(form, "This takes about ten to twenty seconds.");
      });
    });
  });
})();
