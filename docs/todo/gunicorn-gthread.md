# Revisit gunicorn's gthread switch with real data

`web` moved from `--workers 2` (plain sync) to `--worker-class gthread --workers 2 --threads 8`
(`entrypoint.sh`) after directly observing the problem it's meant to fix: a single dashboard page
load fires ~10 parallel API calls (`dashboard.js`'s `Promise.all`), which saturated both sync
workers by itself and queued a trivial ~50ms query behind it for 38-158s wall-clock (queueing time,
not query cost). Not yet verified this actually resolves it in practice — revisit once there's
real request-latency/queue-time data (Datadog APM) from normal usage, not just the one-off manual
test that motivated the change.

**Deliberately parked, no action needed until there's real traffic** (confirmed with the user
2026-09-11) — there isn't any yet, so there's no real data to check against and a synthetic load
test was declined in favor of waiting. If dashboard load ever feels slow or times out again, this
is the first thing to check: in Datadog APM for the `osm-monitor` (web) service, look at request
latency/duration for `/api/changesets/*` endpoints split from queueing/wait time if that's exposed
per-span, and gunicorn's own worker/thread saturation (busy vs idle threads) if visible. The
concrete symptom that would mean gthread *isn't* enough: many concurrent requests (e.g. a page
load's ~10 parallel calls, or several users at once) showing high wall-clock time despite the
underlying query itself being fast in the DB — that's the queueing signature this change was
meant to fix, same as the 38-158s-for-a-50ms-query incident that motivated it. If that shows up
again with gthread already in place, the next lever is more threads/workers.

**Correction (2026-09-17):** the original note here said "mind Postgres's `max_connections=100`
headroom" — that number is stale. Since moving to the `timescaledb-ha` image, the auto-tuner sets
`max_connections=25`, and idle baseline usage (Datadog agent + TimescaleDB background workers +
app) already accounts for ~10 of those before a single dashboard request lands. Adding
workers/threads without also raising `max_connections` explicitly (it's already overridden via
other `-c` flags in `docker-compose.yml`'s `command:`) risks `FATAL: sorry, too many clients
already` under concurrency, which would surface as a 500, not as slowness — check this before
scaling the thread count up.
