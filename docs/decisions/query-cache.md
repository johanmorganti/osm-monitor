# Query cache: ClickHouse's, 5 minutes

Decided 2026-10-16.

API queries run with ClickHouse's query cache (`QUERY_SETTINGS` in
`changesets/analytics/clickhouse/backend.py`): an identical query, same SQL and same parameter
values, within `query_cache_ttl` (300 s) is answered from ClickHouse's RAM. Queries calling
`now()` are not cached (`query_cache_nondeterministic_function_handling = 'ignore'`), and
`check_backend_parity` clears the settings, so parity always compares fresh results.

**Cost: API results can trail ingestion by up to 5 minutes.** Live data arrives every minute, so
a repeated view can miss the last few minutes of changesets.

## Why this cache, and why only this one

Replaying a week of API traffic from APM (2026-10-09 to 10-16: 4,244 requests, 3,694 s of server
time) against a cache keyed on the full URL:

| TTL | requests served from cache | server time saved |
|---|---|---|
| 1 min | 9.5% | 9% |
| 5 min | 18% | 13% |
| 1 h | 32% | 19% |
| 24 h | 47% | 26% |

The slow requests are mostly unique: of 126 over 5 s, 23 repeated the same URL within an hour
(mostly retries after a timeout). A cache doesn't make them faster; contributor-ordered data and
cheaper `FINAL` do ([TODO](../todo/contributor-queries.md)).

- **ClickHouse's cache, not one in Django**: it is shared by every gunicorn worker and thread, its
  key includes the parameter values (checked: two contributors, same SQL, two entries), it needs
  no new service, and it is one setting. Django's in-process cache would be split per worker
  (2 workers, so about half the hits) and would only add skipping the HTTP round trip to ClickHouse
  and the JSON encoding, which are small next to the query (a cached query answered in ~10 ms). A
  shared Django cache would need Redis or memcached: a new service for the same hits.
- **Not Cloudflare (HTTP caching)**: it would need `Cache-Control` on API responses, a Cloudflare
  Cache Rule (JSON isn't cached by default) and dropping `Vary: Cookie`, for the same small share
  of repeats at this traffic. Worth revisiting if traffic grows, for ranges ending two or more days
  ago, which barely change: [TODO](../todo/cloudflare-cache.md).

Limits (server defaults, `system.server_settings`): 1 GiB in total, 1 MiB per result (a larger
result is just not cached), 1,024 entries.
