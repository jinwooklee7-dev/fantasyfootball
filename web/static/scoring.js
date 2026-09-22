/* Scoring format switch.
 *
 * Every points cell carries all three formats as data attributes, so switching
 * is a DOM read, not a page load. That is deliberate: the generated static site
 * has no server to read a query parameter, and rendering three copies of nine
 * hundred pages to support a toggle would be absurd.
 *
 * The choice is remembered per browser. Storage is wrapped because it throws in
 * a private window and comes back empty when site data is cleared -- the page
 * must work either way.
 */
(function () {
  "use strict";

  var KEY = "ffdash:scoring";
  var VALID = ["ppr", "half", "std"];

  function remembered() {
    try {
      var saved = window.localStorage.getItem(KEY);
      return VALID.indexOf(saved) !== -1 ? saved : null;
    } catch (e) {
      return null;
    }
  }

  function remember(value) {
    try {
      window.localStorage.setItem(KEY, value);
    } catch (e) {
      /* Private window or blocked storage: the switch still works, it just
         will not survive a reload. */
    }
  }

  function apply(format) {
    if (VALID.indexOf(format) === -1) {
      format = "ppr";
    }
    document.body.setAttribute("data-scoring", format);

    var cells = document.querySelectorAll(".pts");
    for (var i = 0; i < cells.length; i++) {
      var value = cells[i].getAttribute("data-" + format);
      if (value !== null) {
        cells[i].textContent = value;
      }
    }

    var buttons = document.querySelectorAll(".scorelink");
    for (var j = 0; j < buttons.length; j++) {
      var on = buttons[j].getAttribute("data-scoring") === format;
      buttons[j].classList.toggle("on", on);
      buttons[j].setAttribute("aria-pressed", on ? "true" : "false");
    }

    // Column headers read "PPR/g", "½ PPR/g", "Std/g".
    var labels = { ppr: "PPR", half: "½ PPR", std: "Std" };
    var heads = document.querySelectorAll(".score-head");
    for (var k = 0; k < heads.length; k++) {
      heads[k].textContent = labels[format] + "/g";
    }

    remember(format);
  }

  function init() {
    var saved = remembered();
    if (saved) {
      apply(saved);
    }
    document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest(".scorelink");
      if (!button) {
        return;
      }
      event.preventDefault();
      apply(button.getAttribute("data-scoring"));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
