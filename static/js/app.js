/* Small progressive-enhancement helpers: colour scheme toggle and the
   off-canvas sidebar used on narrow screens. */
(function () {
  "use strict";

  var STORAGE_KEY = "dgc-theme";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    document.querySelectorAll("[data-theme-icon]").forEach(function (icon) {
      icon.className = theme === "dark" ? "bi bi-sun" : "bi bi-moon-stars";
    });
  }

  function storedTheme() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      return null;
    }
  }

  applyTheme(storedTheme() || "light");

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(storedTheme() || "light");

    var toggle = document.querySelector("[data-theme-toggle]");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var next =
          document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
        try {
          localStorage.setItem(STORAGE_KEY, next);
        } catch (err) {
          /* storage unavailable — the theme still applies for this page */
        }
        applyTheme(next);
      });
    }

    var shell = document.querySelector(".app-shell");
    if (shell) {
      document.querySelectorAll("[data-sidebar-toggle]").forEach(function (button) {
        button.addEventListener("click", function () {
          shell.classList.toggle("sidebar-open");
        });
      });
      var backdrop = shell.querySelector(".sidebar-backdrop");
      if (backdrop) {
        backdrop.addEventListener("click", function () {
          shell.classList.remove("sidebar-open");
        });
      }
    }

    document.querySelectorAll("[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) {
          event.preventDefault();
        }
      });
    });
  });
})();
