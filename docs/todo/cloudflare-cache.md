# API: HTTP caching at Cloudflare

Deferred 2026-10-16, when the ClickHouse query cache was added instead
([query-cache](../decisions/query-cache.md), which has the traffic replay behind the decision).

Today every API response goes through Cloudflare uncached (`cf-cache-status: DYNAMIC`): no
`Cache-Control` header, and `Vary: Cookie`. Only the hashed static files are cached there (one
year, `immutable`, already `HIT`).

## When it becomes worth it

At 2026-10 traffic (~4,200 API requests a week), the ClickHouse query cache catches most of
what HTTP caching would: about 18% of requests are a repeat within 5 minutes. Cloudflare would
add value when:

- traffic grows enough that repeats of the same URL from different visitors are common (the
  default dashboard view, popular contributors or countries), or
- the 4G uplink is the bottleneck: a Cloudflare hit never crosses the tunnel, so it is immune to
  the overnight drops seen on 2026-10-15 and saves upload bandwidth. Geo responses are 0.2-2 MB.

## Design

1. **Cache only what doesn't change.** Ranges ending two or more days ago barely change
   (changesets close within 24 h; old-dated rows from resurfaced comments are rare, see
   [old-dated-rows](../decisions/old-dated-rows.md)): send
   `Cache-Control: public, max-age=300, s-maxage=86400` for those. Anything that reaches today or
   yesterday, and requests without dates (the default "last 7 days"), get `no-store` or a short
   `s-maxage` (60 s) at most. Decide this in one place in `changesets/api/` (a mixin or a
   decorator on the views), from the resolved `Filters`, not per endpoint.
2. **Drop `Vary: Cookie` on the API.** It comes from DRF's default `SessionAuthentication`
   touching the session. The API has no authentication, so set
   `DEFAULT_AUTHENTICATION_CLASSES: []` in `REST_FRAMEWORK` (check that `/api/docs/` and the MCP
   endpoint still work). With `Vary: Cookie` set, Cloudflare doesn't cache.
3. **Add a Cloudflare Cache Rule** for `osm-monitor.johan.sh/api/*`: "eligible for cache",
   respecting the origin's `Cache-Control`. Cloudflare doesn't cache JSON by default. The cache
   key is the full URL with its query string, which is what the API varies on.
4. **Leave out** the `/mcp` endpoint (POST), `/api/changesets/` (raw records, paginated) unless
   measured as worth it, and error responses (`408`, `400`: `no-store`).

## Checks

- `curl -sI` twice on an old range: `cf-cache-status: MISS`, then `HIT`, and a recent range
  stays `DYNAMIC` or `BYPASS`.
- Behavior neutral: response bodies byte-identical before and after (only headers change), and
  the OpenAPI schema diff is empty.
- A deploy doesn't need a purge: the data doesn't change with the code. A change that alters
  response contents does: purge `api/*` in Cloudflare after deploying it, or old bodies are served
  for up to a day. Document that in `docs/DEPLOYMENT.md` when this ships.
