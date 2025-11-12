(() => {
  const STORAGE_KEY = "ferraen-theme";
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

  const applyTheme = (theme) => {
    const normalized = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-bs-theme", normalized);
    document.documentElement.style.colorScheme = normalized;
    localStorage.setItem(STORAGE_KEY, normalized);
    document.body?.classList.toggle("theme-dark", normalized === "dark");
    document.body?.classList.toggle("theme-light", normalized === "light");
  };

  const updateToggle = (theme) => {
    const toggle = document.querySelector("[data-theme-toggle]");
    if (!toggle) return;
    const icon = toggle.querySelector(".bi");
    const label = toggle.querySelector("[data-theme-label]");
    const isDark = theme === "dark";
    toggle.setAttribute("aria-pressed", String(isDark));
    toggle.setAttribute("data-theme", theme);
    if (icon) {
      icon.classList.toggle("bi-moon-stars", !isDark);
      icon.classList.toggle("bi-sun", isDark);
    }
    if (label) {
      label.textContent = isDark ? "Dark" : "Light";
    }
  };

  const init = () => {
    const stored = localStorage.getItem(STORAGE_KEY);
    const theme = stored || (prefersDark.matches ? "dark" : "light");
    applyTheme(theme);
    updateToggle(theme);

    const toggle = document.querySelector("[data-theme-toggle]");
    toggle?.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      updateToggle(next);
    });
  };

  prefersDark.addEventListener("change", (event) => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return; // Respect explicit choice
    const theme = event.matches ? "dark" : "light";
    applyTheme(theme);
    updateToggle(theme);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
