# WeldLog

This repository contains the WeldLog project. The front-end now uses a Bronze & Midnight Bootstrap theme compiled through Sass.

## Ownership transition notes (for IT)

This section is intended as a quick operational handoff for the internal IT team.

### Stack overview

- **Framework:** Django 4.2 (`pipeledger` project)
- **Python apps:** `accounts`, `organizations`, `projects`, `welds`, `drive`, `documents`
- **Background jobs:** Celery with Redis broker/result backend
- **Frontend assets:** Sass compiled to `static/css/main.css`
- **Database default in repo:** SQLite (`db.sqlite3`)

### Required environment variables

The app loads environment variables from a local `.env` file when present.

- `OPENAI_API_KEY` (**required**; app raises `ImproperlyConfigured` if missing)
- `OPENAI_MODEL` (optional; default `gpt-4`)
- `CELERY_BROKER_URL` (optional; default `redis://localhost:6379/0`)
- `CELERY_RESULT_BACKEND` (optional; default `redis://localhost:6379/1`)
- `MAX_UPLOAD_SIZE_MB` (optional; default `50`)

### Local/dev runbook

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   npm install
   ```

2. Create `.env` in repo root and set at least `OPENAI_API_KEY`.

3. Apply migrations and create an admin account:

   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

4. Run the web app:

   ```bash
   python manage.py runserver
   ```

5. Run background worker(s) when async features are used:

   ```bash
   celery -A pipeledger worker -l info
   ```

### Production hardening checklist

Before production ownership is finalized, update these defaults in `pipeledger/settings.py`:

- Replace hardcoded `SECRET_KEY` with environment-managed secret.
- Set `DEBUG = False`.
- Populate `ALLOWED_HOSTS` for deployed domains.
- Confirm database settings for production (current default is SQLite).
- Ensure Redis is available for Celery broker/backend.

### Key operational commands

```bash
# Run tests
python manage.py test

# Rebuild CSS
npm run build:sass

# Watch Sass during UI work
npm run watch:sass
```

## Building styles

To compile the theme CSS locally:

```bash
npm install
npm run build:sass
```

The build script outputs the compressed CSS bundle to `static/css/main.css`. During development you can run `npm run watch:sass` to rebuild on changes.

## Running tests

Backend tests can be executed with:

```bash
python manage.py test
```
