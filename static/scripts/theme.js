(function () {
  const THEME_KEY = 'ferraen-theme';
  const root = document.documentElement;

  function setTheme(theme) {
    root.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_KEY, theme);
  }

  function toggleTheme() {
    const current = root.getAttribute('data-theme') || 'light';
    setTheme(current === 'light' ? 'dark' : 'light');
  }

  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const stored = localStorage.getItem(THEME_KEY);
  if (stored) {
    setTheme(stored);
  } else if (prefersDark) {
    setTheme('dark');
  } else {
    setTheme('light');
  }

  window.FerraenTheme = {
    toggle: toggleTheme,
    set: setTheme,
  };

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-toggle]')
      .forEach(btn => btn.addEventListener('click', toggleTheme));

    const commandPalette = document.querySelector('[data-command-palette]');
    const paletteInput = commandPalette?.querySelector('input');

    function openPalette() {
      if (!commandPalette) return;
      commandPalette.dataset.open = 'true';
      commandPalette.setAttribute('aria-hidden', 'false');
      paletteInput?.focus();
    }

    function closePalette() {
      if (!commandPalette) return;
      commandPalette.dataset.open = 'false';
      commandPalette.setAttribute('aria-hidden', 'true');
    }

    document.addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openPalette();
      }
      if (event.key === 'Escape') {
        closePalette();
      }
    });

    commandPalette?.addEventListener('click', (event) => {
      if (event.target.hasAttribute('data-close-palette')) {
        closePalette();
      }
    });

    commandPalette?.querySelector('[data-command-input]')?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closePalette();
      }
    });
  });
})();
