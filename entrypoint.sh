#!/bin/sh
set -e

python manage.py collectstatic --no-input

# Datadog APM is optional: wrap the process in ddtrace-run only when tracing
# is enabled (docker-compose.datadog.yml sets DD_TRACE_ENABLED=true).
TRACE=""
if [ "${DD_TRACE_ENABLED:-false}" = "true" ]; then
    TRACE="ddtrace-run"
fi

# migrate is NOT run here — it's a separate one-shot `migrate` service in
# docker-compose.yml that web/poller depend on completing first. Running it
# per-service-start (the old behavior) raced when web and poller started
# together: a non-idempotent migration could execute concurrently from both
# before either committed it as applied, producing duplicate side effects.

if [ "$#" -eq 0 ]; then
    # gthread, not plain sync workers: this workload is almost entirely
    # I/O-bound (waiting on the database), and a single dashboard page load
    # fires ~10 parallel API calls (dashboard.js's Promise.all) — with only
    # 2 sync workers, one page load alone can saturate both and queue every
    # other request behind it (observed: a trivial ~50ms query taking 38-158s
    # wall-clock under concurrent dashboard load, all queueing, not query
    # cost). Threads share a worker's memory instead of each duplicating the
    # full Django import footprint, so 2 workers x 8 threads = 16 concurrent
    # request slots costs only modestly more than today's 2 plain workers,
    # not ~8x more the way reaching 16 via --workers 16 would.
    # Re-measured 2026-10-02 with ClickHouse as the backend (4 vCPUs): the
    # Overview page's 13 calls over a 1-year range take 6.7s in parallel
    # vs 9.6s summed sequentially, and the difference is ClickHouse CPU
    # (each query already uses max_threads=4), not request queueing — 16
    # slots exceed one page load. More threads would only add contention
    # for the same cores; the levers are cheaper queries and lazy-loading
    # widgets, not more slots.
    # --timeout 40: must stay comfortably above ClickHouse's 30 s query cap
    # (max_execution_time, changesets/analytics/clickhouse/backend.py) so
    # ClickHouse cancels a slow query cleanly, and the API answers 503,
    # before gunicorn would SIGKILL the worker out from under it.
    exec $TRACE gunicorn osm_changeset_api.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --worker-class gthread --workers 2 --threads 8 --timeout 40
fi

exec $TRACE "$@"
