# WeldLog

This repository contains the WeldLog project. The front-end now uses a Bronze & Midnight Bootstrap theme compiled through Sass.

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
