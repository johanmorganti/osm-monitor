# No Content-Security-Policy (2026-10-31)

Decided not to add one. The site sends HSTS, `X-Frame-Options: DENY`, `nosniff` and a same-origin
referrer policy (Django's security middleware); there is deliberately no `Content-Security-Policy`.

## What was weighed

A CSP is a second line of defence against XSS: a `script-src` without `'unsafe-inline'` would stop
injected script from running even if a future bug lets markup through (the autocomplete bug fixed on
2026-10-28 was one). What it would have taken, from a survey of the pages that day:

- all scripts are the site's own files, plus three inline ones (the RUM init in `base.html`, the
  poller page's refresh function, Swagger's init built by drf-spectacular), so those had to move
  into static files or take a per-request nonce;
- about 25 `style="..."` attributes and Leaflet/MapLibre/Chart.js inline styles, so `style-src`
  would have needed `'unsafe-inline'` anyway;
- origins to allow and keep in step with the environment: `tiles.openfreemap.org`,
  `tile.openstreetmap.org`, the RUM proxy (`DD_RUM_PROXY_URL`), `blob:` workers for MapLibre and
  session replay;
- a middleware of our own (Django 5.2 has no built-in CSP, 6.0 does), and a report-only rollout
  watched through RUM before enforcing.

## Why not

The pages render no user-supplied HTML apart from the places already escaped (`textContent`, the
autocomplete fix), there are no accounts or sessions to steal, and the cost is a permanent
maintenance edge: every new map source, library or analytics script would break the pages until the
policy is updated.

**How to apply:** don't add a CSP as part of a hardening pass. If the site ever gets logins or
user-submitted content, reopen this, and start from Django 6.0's built-in `SECURE_CSP` with a
report-only period. Keep escaping at the sink: build DOM with `textContent`, never `innerHTML` with
API data.
