# Ferraen Theme System

The Ferraen interface is powered by a token-driven layer on top of the Bootstrap 5.3 CDN. The goal is to keep delivery simple while providing a premium, high-contrast visual language across light and dark modes.

## Token architecture
- **`ferraen.theme.css`** defines brand tokens (`--color-primary`, `--surface`, `--text`, etc.) and maps them to Bootstrap custom properties (`--bs-*`).
- Each mode (`:root` for light and `:root[data-bs-theme="dark"]` for dark) overrides only the tokens that change between themes.
- Component-specific utilities (e.g., `.kpi-card`, `.timeline`, `.hero`) reference the same tokens to keep the system cohesive.

## Usage
1. Load Bootstrap 5.3, Bootstrap Icons, Google Fonts, and `ferraen.theme.css` in that order.
2. Ensure the root `<html>` element has a `data-bs-theme` attribute. The theme toggle script (`ferraen.theme.js`) updates this attribute and persists the choice to `localStorage`.
3. Wrap application content in the provided `base_shell.html` template to inherit the header, sidebar, and responsive layout structure.

## Extending the system
- **New colors:** Introduce a token (e.g., `--color-positive`) inside both light and dark scopes, then map it to Bootstrap (`--bs-success` or component styles) to keep parity.
- **Components:** Build custom components by reusing existing tokens and elevation utilities (`.elev-1`…`.elev-3`, `.hover-lift`) to preserve depth cues.
- **Motion:** Reference the timing tokens (`--dur-*`, `--easing`) for transitions and respect `prefers-reduced-motion` by using the provided mixin pattern.
- **Spacing:** Use CSS Grid helpers defined in the templates and lean on Bootstrap’s spacing utilities for quick adjustments.

By centralizing decisions in the token file, Ferraen maintains a consistent brand experience without introducing a build step.
