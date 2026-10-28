# Front-end code is served from the site, not from CDNs

Decided 2026-10-28. Every script and stylesheet the pages load comes from the site's own static
files. The one exception is the Datadog RUM SDK, loaded through the first-party RUM proxy when
`DD_RUM_PROXY_URL` is set (`changesets/context_processors.py`). Map tiles and styles still come
from OpenFreeMap and openstreetmap.org: they are data, not code.

## What changed

- **Tailwind:** compiled at image build in the Dockerfile's `css` stage into `static/css/site.css`
  (`tailwind/tailwind.config.js`, default theme, no plugins), instead of the Play CDN script
  (`cdn.tailwindcss.com`). The poller status page uses the same file, and the hand-built
  `static/output.css` (Tailwind 3.4.6, built once) is gone.
- **Chart.js, Leaflet, MapLibre GL, maplibre-gl-leaflet:** copied into `static/vendor/`, one
  directory per package and version. Provenance, hashes and how to update are in
  [`static/vendor/README.md`](../../static/vendor/README.md).
- **Swagger UI:** in `static/vendor/` too, pinned to 5.33.2, given to drf-spectacular by
  `ApiDocsView` in `changesets/views.py`. drf-spectacular's default is
  `swagger-ui-dist@latest` on jsDelivr, so each new release ran on `/api/docs/` unreviewed.

## Why

A script from another host runs with the page's full access. Two of the scripts were not
pinned at all: the Tailwind Play CDN can't carry an integrity hash, since it is a compiler that
generates CSS in the browser (and is meant for development only), and Swagger UI was loaded as
`@latest`. The four libraries already had integrity hashes, so a modified file would have been
refused, but the page still depended on unpkg and jsDelivr being up and reachable. Serving them
from the site removes that dependency, and the files get the same hashed, one-year immutable
caching as the site's own JS (whitenoise's manifest storage).

## How it was checked

- The copied library files match the integrity hashes the templates pinned before (sha256 or
  sha384), so they are the exact files the pages loaded. The npm tarballs matched the registry's
  `sha512`.
- The Play CDN was serving Tailwind 3.4.17, the version the build pins, so the same compiler
  produces the CSS. It now scans the templates and `static/js` at build time instead of the live
  page. The one class found only in Swagger UI's own markup, `float-right`, is defined
  identically in Swagger UI's stylesheet.
- With the old and new images side by side on the same ClickHouse, the 43 API, schema and MCP
  responses were byte-identical. The 6 page differences were only the changed asset tags (plus
  per-request CSRF tokens). Every asset each page references, including Leaflet's CSS images,
  answered 200.

## How to apply

- **Tailwind classes must appear whole** in `changesets/templates/` or `static/js/`: the build
  finds them by scanning those files. `` `bg-${color}-500` `` is never generated, so write each
  full class name. A new place that holds class names goes into the `content` list in
  `tailwind/tailwind.config.js`.
- **A new library goes into `static/vendor/`**, following the README there, not onto a CDN.
- The Tailwind CLI is downloaded when the image builds (pinned by checksum, then cached by
  BuildKit). Outside Docker, `make css` builds the same file with a local `./tailwindcss`.
