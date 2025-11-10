# Ferraen Interface System

## Migration plan (Tailwind path)
- Replace Bootstrap with a Tailwind-powered utility layer that compiles from `static/styles/global.css` into `static/styles/app.css` via PostCSS.
- Register the new token-driven Tailwind theme (`tailwind.config.js`) and purge templates under `templates/` and documentation under `/ui`.
- Adopt the design system components in `templates/ui/` across feature templates, gradually migrating existing Django views to extend `templates/base.html`.
- Remove legacy Bootstrap references from templates and ensure static asset pipeline serves the Tailwind build output.

## Brand foundations
- **Vibe:** modern industrial + premium tech — high-contrast, minimal ornament, structured grid, micro-depth.
- **Typography:** Sora for headings (confidence) and Inter for body copy (clarity). Font files are loaded via Google Fonts in `static/styles/global.css`.
- **Color system:** Defined as CSS variables in `static/styles/tokens.css` with light/dark parity. Primaries lean toward electric indigo (#4B6BFB / #7B94FF) with cyan accent (#22D3EE / #06B6D4).
- **Spacing, radii, shadows:** Tokenized in rem/px to create consistent cards, overlays, and focus states.
- **Motion:** `--motion-standard` (150ms) and `--motion-emphasis` (250ms) using an eased spring curve for hover/press feedback.

## File map
- `static/styles/tokens.css` – design tokens and theme variables.
- `static/styles/global.css` – base styles, component primitives, utilities (compiled into `app.css`).
- `static/scripts/theme.js` – theme toggle, command palette wiring.
- `static/scripts/ui.js` – modal + toast helpers.
- `tailwind.config.js`, `postcss.config.js`, `package.json` – Tailwind/PostCSS build pipeline.
- `templates/base.html` – application shell (header, sidebar, main, footer, command palette, toasts).
- `templates/ferraen/` – exemplar product pages for every key flow.
- `templates/ui/` – HTML partials documenting the component library (Button, Card, Input, Select, Modal, Tabs, Toast, Table, EmptyState, KPI card).

## Theming
1. Theme tokens live in `static/styles/tokens.css`. Light mode is declared on `:root`; dark mode overrides appear under `[data-theme="dark"]`.
2. `theme.js` sets the `data-theme` attribute on `<html>` based on `prefers-color-scheme` and a persisted `localStorage` key (`ferraen-theme`).
3. Any template can drop a `[data-theme-toggle]` element to toggle instantly. Dark mode applies to every component thanks to variable-driven colors.

## Using tokens & components
- Include `static/styles/app.css` and the optional JS helpers in your template.
- Compose UI by including partials, e.g. `{% include 'ui/button.html' with label='Save' variant='primary' %}`.
- Cards, inputs, tabs, tables, and toasts rely on CSS variables. Extend by adding modifier classes (`card--warning`, `badge--info`) or stacking utilities defined in `global.css`.
- For charts, mount a library like Recharts within card shells and draw colors from the tokens (`var(--color-primary)`). Skeleton states are provided via `.surface-glass` or `.empty-state` wrappers.

## Extending the system
- Add new Tailwind utilities by editing `tailwind.config.js`. Because theme values map to CSS variables, new utilities automatically inherit tokenized values.
- When introducing a new component, create a partial under `templates/ui/` and reference it in feature templates. Document usage at the top of the partial in a Django `{# comment #}`.
- Keep motion accessible: respect `prefers-reduced-motion` by leveraging the global rule already in `global.css`.

## Before / After notes
- **`templates/base.html`** now renders the premium Ferraen shell (sticky header, collapsible-ready sidebar, command palette, toast stack). Bootstrap references have been removed.
- **Auth flow** now lives in `templates/ferraen/auth.html` with a split hero + form experience replacing the minimal Bootstrap login.
- **Dashboard, Tables, Detail, Form, Settings** routes should swap their `extends` to the new base and align data with the provided markup for instant design uplift.
- **Landing** gets a hero, trust strip, and features grid in `templates/ferraen/landing.html`, replacing the old basic marketing stub.

## Integration guides
### Django
1. Add `STATICFILES_DIRS` entry (if not already) that points to the root `static/` directory.
2. Build the Tailwind bundle: `npm install` then `npm run build`. Collect static assets with `python manage.py collectstatic` for production.
3. Ensure templates extend `templates/base.html` or import the component partials under `templates/ui/`.
4. Load static assets using `{% load static %}` and include `<script src="{% static 'scripts/theme.js' %}"></script>`.

### React
1. Copy `static/styles/tokens.css` and `global.css` into your `src/` directory (e.g., `src/styles/`).
2. Install Tailwind: `npm install tailwindcss postcss autoprefixer @tailwindcss/forms @tailwindcss/typography` and copy the provided `tailwind.config.js` and `postcss.config.js`.
3. Import the CSS into your entry file (`import './styles/global.css'`). Components can be recreated using the markup from `templates/ui/` translated into JSX.
4. Wire `theme.js` logic or replicate it with React state to respect `prefers-color-scheme` and persist user preference.

## Development scripts
```bash
npm install        # install Tailwind + PostCSS deps
npm run dev        # watch mode (writes to static/styles/app.css)
npm run build      # production build
```

## Visual reference
- Dashboard hero + KPI grid: `templates/ferraen/dashboard.html`
- Landing hero: `templates/ferraen/landing.html`
- Form wizard: `templates/ferraen/form.html`

Ferraen now presents as a modern, premium-grade control surface ready to evolve into a documented design system.
