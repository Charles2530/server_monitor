(() => {
  "use strict";
  const storageKey = "ilab.appearance";
  const root = document.documentElement;

  function applyTheme(theme) {
    const dark = theme === "dark";
    root.dataset.theme = dark ? "dark" : "light";
    document.querySelector('meta[name="color-scheme"]').content = root.dataset.theme;
    document.querySelector('meta[name="theme-color"]').content = dark ? "#111b19" : "#f5f6f8";
    const button = document.getElementById("theme-toggle");
    if (button) {
      const label = dark ? "浅色模式" : "深色模式";
      button.querySelector(".theme-toggle-label").textContent = label;
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    document.dispatchEvent(new CustomEvent("appearance-change"));
  }

  // Apply before styles load so a saved dark theme does not flash light on reload.
  let savedTheme;
  try { savedTheme = localStorage.getItem(storageKey); } catch { /* Storage may be blocked. */ }
  applyTheme(savedTheme);
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(root.dataset.theme);
    document.getElementById("theme-toggle").addEventListener("click", () => {
      applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
      try { localStorage.setItem(storageKey, root.dataset.theme); } catch { /* Keep this session usable. */ }
    });
  });
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey || event.key === null) applyTheme(event.newValue);
  });
})();
