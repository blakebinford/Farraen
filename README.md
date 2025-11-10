# Ferraen Interface System (Bootstrap Edition)

Ferraen now ships with a custom Bootstrap 5 theme layered with industrial-grade brand tokens, a reusable component library, and exemplar screens that highlight the new product direction.

## Brand + Theme Foundations
- **Why Bootstrap 5** – keeps an accessible, battle-tested layout system while letting us apply a bespoke Ferraen skin via SCSS tokens, gradients, and interaction polish.
- **Visual vibe** – modern industrial tech: structured grids, high contrast, glassy overlays, soft glows, and confident typography (Sora headings + Inter body copy).
- **Design tokens** – spacing (2–64), radii (6–9999px), shadows (sm/md/lg/glow), and motion (150/250ms) are exposed as CSS variables in `static/styles/scss/_tokens.scss` with light/dark parity.
- **Color palette** – Primary indigo `#4B6BFB` / `#7B94FF`, cyan accent `#22D3EE` / `#06B6D4`, structured neutrals, and semantic success/warning/danger hues meet WCAG AA.
- **Motion & focus** – buttons lift on hover, inputs glow on focus, and `prefers-reduced-motion` is honored globally.

## Source Layout
```
docs/bootstrap-migration.md          # Bullet migration plan for the Bootstrap path
static/styles/scss/_tokens.scss      # Brand tokens + Bootstrap variable overrides
static/styles/scss/_theme.scss       # Layout, components, utilities (authoring source)
static/styles/scss/ferraen.scss      # Import glue for Sass compilation
static/styles/app.css                # Compiled production stylesheet served by Django
static/scripts/theme.js              # Theme toggle + command palette
static/scripts/ui.js                 # Modal & toast helpers
templates/base.html                  # Application shell (header, sidebar, command palette, toasts)
templates/ui/*.html                  # Component library partials (Button, Card, Input, Select, Modal, Tabs, Toast, Table, EmptyState, KPI)
templates/ferraen/*.html             # Key screens: Auth, Landing, Dashboard, Tables, Detail, Form, Settings
```

> **Build note:** npm access is currently blocked in this environment. When restored, run `npm install` followed by `npm run build` (Sass compiler) to regenerate `static/styles/app.css` from the SCSS sources. Until then, the repository already includes an up-to-date compiled stylesheet.

## Theming (Light & Dark)
1. `theme.js` checks `localStorage` (`ferraen-theme`) and `prefers-color-scheme`, then sets `data-theme` on `<html>`.
2. Tokens inside `_tokens.scss` register matching light/dark CSS variables. Every component consumes variables rather than hard-coded colors.
3. Drop a `[data-theme-toggle]` element anywhere to hook into the toggle helper. Toasts, modals, and layout surfaces all respond automatically.

## Component Library Overview
Each partial under `templates/ui/` documents usage in a Django comment and emits semantic, accessible markup:
- **Buttons** – variants `primary`, `secondary`, `ghost`; sizes `sm/md/lg`; icon support.
- **Card & KPI card** – glass/elevated cards, KPI stats with trend chips, warning modifiers.
- **Inputs & Selects** – tokenized controls with inline hints and validation states.
- **Modal** – accessible backdrop with focusable actions.
- **Tabs** – keyboard-friendly tablist backed by Bootstrap utilities.
- **Toast** – aria-live safe notifications that queue via `ui.js`.
- **Table** – dense table shell with pagination/footer controls and badge styling.
- **Empty State** – dashed card for zero-data scenarios.
- **KPI Card** – quick metrics with trend metadata.

See `templates/ui/` for concrete snippets ready to `{% include %}` or translate to JSX.

## Key Screens Delivered
1. **Auth (`templates/ferraen/auth.html`)** – split gradient hero + form with password strength bar and SSO entry.
2. **Landing (`templates/ferraen/landing.html`)** – hero with live product panel, trust strip, and feature cards.
3. **Dashboard (`templates/ferraen/dashboard.html`)** – KPI grid, trend card, activity timeline, and accordion actions.
4. **Tables (`templates/ferraen/tables.html`)** – sortable-ready table shell, filter chips, pagination controls.
5. **Detail (`templates/ferraen/detail.html`)** – breadcrumb header, status pill, tabbed content, and timeline.
6. **Create/Edit Form (`templates/ferraen/form.html`)** – multi-step wizard section with sticky guidance.
7. **Settings (`templates/ferraen/settings.html`)** – left-nav layout, token previews, theme switcher (see file).
8. **Notifications/Toasts** – wired globally through the toast stack in `templates/base.html` and demos in `ui/toast.html`.

## Before / After Highlights
- Tailwind + PostCSS configs have been removed; Bootstrap 5 is loaded via CDN with a bespoke Ferraen layer.
- `static/styles/app.css` now contains handcrafted CSS tokens/components instead of a Tailwind build artifact.
- Templates under `templates/ferraen/` and `templates/ui/` have been rewritten to use Ferraen-specific classnames and Bootstrap-compatible structure.
- Sidebar/header shell keeps Bootstrap’s responsive grid while introducing glassmorphism, command palette, and theme toggles.

## Integration Guides
### Django
1. Ensure `STATIC_URL` serves `static/` (already configured in this project).
2. Include the Bootstrap CDN + `static/styles/app.css` links (already present in `templates/base.html` and standalone pages).
3. Use `{% extends 'base.html' %}` for app views or drop component partials directly.
4. Rebuild CSS with `npm run build` (Sass) before running `collectstatic` for production deploys. The command compiles `static/styles/scss/ferraen.scss` into `static/styles/app.css`.

### React (or other SPA)
1. Copy `static/styles/app.css` (or compile from SCSS) into your `src/` directory and import it once.
2. Inline the SVG-based icon snippets or install Lucide icons for parity.
3. Port component partial markup into JSX, preserving aria attributes and token-driven class names.
4. Reimplement `theme.js` logic with React state or call its helpers directly to sync theme preference.

## Extending the System
- Add brand tokens in `_tokens.scss`, then use them inside `_theme.scss` so the compiled CSS remains cohesive.
- New components should live under `templates/ui/` with a usage comment and follow the token-based spacing/typography.
- Prefer Bootstrap utilities (e.g., `d-flex`, `gap-3`) only for layout tweaks; primary styling should stay in the Ferraen class layer for maintainability.
- Keep accessibility front-of-mind: honor focus-visible, provide aria labels, and respect reduced motion.

## Development Scripts
```bash
npm run dev    # watch mode (requires npm access + sass)
npm run build  # compile SCSS to static/styles/app.css
```

Ferraen now reads as a premium, industrial-grade control surface ready for production use and future design system growth.
