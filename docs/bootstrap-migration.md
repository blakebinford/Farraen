# Ferraen UI Migration Plan (Bootstrap 5)

- **Why Bootstrap 5**: Keeps a stable, accessible component API while letting us layer a bespoke industrial aesthetic through SCSS overrides and tokens. CDN delivery avoids bundler lock-in and works well with Django templates we already have.
- **Tokenize the brand**: Define spacing, radii, color, shadow, and motion tokens in `static/styles/scss/_tokens.scss`, expose light/dark CSS variables, and map Bootstrap primitive variables to the Ferraen palette.
- **Compile the custom theme**: Author layout + component styling in `_theme.scss`, import tokens via `theme.scss`, and compile to `static/styles/app.css` (run `npm run build` once npm access is restored, or any Sass compiler) so Django serves a single optimized stylesheet.
- **Adopt Bootstrap utilities thoughtfully**: Use the CDN bundle for grid/flex helpers, but favor the Ferraen class layer (`.app-shell`, `.kpi-card`, `.btn--*`) for branded experiences; extend components via partials in `templates/ui/`.
- **Clean up Tailwind**: Remove Tailwind configs, dependencies, and generated CSS; ensure templates no longer reference Tailwind utility classes.
- **Progressive rollout**: Replace pages screen-by-screen (auth, landing, dashboard, tables, detail, forms, settings), validating focus states, dark mode, and responsiveness as each ships.
- **Docs & onboarding**: Update `README.md` with theming guidance, component usage, and integration tips for Django/React engineers. Include before/after notes so teams understand template swaps.
