// dwyt futtató — the window's little bit of script (no inline script: strict CSP).
(function () {
  "use strict";

  // Status tab: refresh the live part every few seconds.
  var live = document.querySelector("[data-poll][data-live]");
  if (live) {
    setInterval(function () {
      fetch(live.getAttribute("data-poll"), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) {
          if (!html) return;
          var doc = new DOMParser().parseFromString(html, "text/html");
          var fresh = doc.querySelector("[data-poll]");
          if (fresh) {
            live.innerHTML = fresh.innerHTML;
            if (!fresh.hasAttribute("data-live")) live.removeAttribute("data-live");
          }
        })
        .catch(function () {});
    }, 3000);
  }

  // Machine tab: follow the test run, reload when it is over.
  if (document.querySelector("[data-trial-running]")) {
    var stage = document.querySelector("[data-trial-stage]");
    var timer = setInterval(function () {
      fetch("/api/proba", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (t) {
          if (stage) stage.textContent = t.stage || "";
          if (!t.running) { clearInterval(timer); location.reload(); }
        }).catch(function () {});
    }, 2000);
  }

  // Machine tab: a scan in the background; show it when it is done.
  if (document.querySelector("[data-machine-scanning]")) {
    var scanTimer = setInterval(function () {
      fetch("/api/gep", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (g) { if (!g.scanning) { clearInterval(scanTimer); location.reload(); } })
        .catch(function () {});
    }, 1000);
  }

  // Mode buttons: the chosen one lights up at once.
  document.querySelectorAll(".segmented input[type=radio]").forEach(function (input) {
    input.addEventListener("change", function () {
      document.querySelectorAll(".segmented .seg").forEach(function (s) { s.classList.remove("on"); });
      input.closest(".seg").classList.add("on");
      document.querySelectorAll("[data-flow-for]").forEach(function (f) {
        f.hidden = f.getAttribute("data-flow-for") !== input.value;
      });
      var unsaved = document.querySelector("[data-unsaved]");
      if (unsaved) unsaved.hidden = input.defaultChecked;
    });
  });

  // Detailed settings: advanced switch, and fields that only matter with another setting.
  var form = document.querySelector("form[data-show-if]");
  if (form) {
    var adv = form.querySelector("[data-toggle-advanced]");
    if (adv) adv.addEventListener("change", function () { form.classList.toggle("show-advanced", adv.checked); });
    var valueOf = function (key) {
      var el = form.elements[key];
      if (!el) return undefined;
      return el.type === "checkbox" ? el.checked : el.value;
    };
    var update = function () {
      form.querySelectorAll("[data-when]").forEach(function (field) {
        var rules = JSON.parse(field.getAttribute("data-when"));
        var ok = rules.some(function (r) { return valueOf(r.key) === r.value; });
        field.classList.toggle("hidden", !ok);
      });
    };
    form.addEventListener("change", update);
    update();
  }

  // a switch that saves itself (Indítás bejelentkezéskor)
  document.querySelectorAll("[data-autosubmit]").forEach(function (input) {
    input.addEventListener("change", function () { input.form.submit(); });
  });

  // Progress bars: the width comes from data-percent (no inline style: strict CSP).
  var bars = function () {
    document.querySelectorAll("[data-bar][data-percent]").forEach(function (b) {
      b.style.width = b.getAttribute("data-percent") + "%";
    });
  };
  bars();

  // Setup steps: follow downloads and the pairing; reload when one is over.
  var watched = document.querySelectorAll("[data-watch][data-running]");
  if (watched.length) {
    var setupTimer = setInterval(function () {
      fetch("/api/telepites", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (s) {
          var over = false;
          watched.forEach(function (w) {
            var key = w.getAttribute("data-watch");
            var st = s[key] || {};
            if (key === "pairing") { if (st.state !== "waiting") over = true; return; }
            if (!st.running) { over = true; return; }
            var bar = w.querySelector("[data-bar]"), text = w.querySelector("[data-text]");
            if (bar) {
              if (st.percent === null || st.percent === undefined) { bar.classList.add("busy"); }
              else { bar.classList.remove("busy"); bar.style.width = st.percent + "%"; }
            }
            if (text) text.textContent = st.text || "";
          });
          if (over) { clearInterval(setupTimer); location.reload(); }
        }).catch(function () {});
    }, 1500);
  }

  // Setup, mode cards: show the chosen mode's picture at once.
  document.querySelectorAll("[data-mode-card]").forEach(function (input) {
    input.addEventListener("change", function () {
      document.querySelectorAll("[data-flow-for]").forEach(function (f) {
        f.hidden = f.getAttribute("data-flow-for") !== input.value;
      });
    });
  });

  // A form that takes a moment: say so at once, and no second press.
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function () {
      form.querySelectorAll("button").forEach(function (b) {
        b.disabled = true;
        b.textContent = form.getAttribute("data-busy");
      });
    });
  });
})();
