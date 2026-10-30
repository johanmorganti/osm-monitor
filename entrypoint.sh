#!/bin/sh
set -e

# Datadog APM is optional: wrap the process in ddtrace-run only when tracing
# is enabled (docker-compose.datadog.yml sets DD_TRACE_ENABLED=true, and builds
# the image with ddtrace).
TRACE=""
if [ "${DD_TRACE_ENABLED:-false}" = "true" ]; then
    if command -v ddtrace-run >/dev/null; then
        TRACE="ddtrace-run"
    else
        echo "DD_TRACE_ENABLED=true but ddtrace isn't installed (image built without DATADOG=true): running untraced" >&2
    fi
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
    # GUNICORN_WORKERS (docker-compose.yml) sizes it to the host's cores.
    # --timeout 40: must stay comfortably above ClickHouse's 30 s query cap
    # (max_execution_time, changesets/analytics/clickhouse/backend.py) so
    # ClickHouse cancels a slow query cleanly, and the API answers 408,
    # before gunicorn would SIGKILL the worker out from under it.
    # --log-config-json: gunicorn's own lines (booting, signals, worker
    # exits) in the same JSON as Django's, with a level; as plain stderr text
    # Datadog filed every one of them as an error.
    # --max-requests: a worker restarts after ~1000 requests (jittered), returning the
    # memory big responses (a world map is 10-20 MB of JSON) left in its heap; the web
    # container peaked at 1.93 of 2 GiB with workers that had never been recycled.
    # --no-control-socket: gunicorn (26) opens a runtime control socket by
    # default; nothing here uses it, so it stays closed.
    exec $TRACE gunicorn osm_changeset_api.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --worker-class gthread --workers "${GUNICORN_WORKERS:-2}" --threads 8 --timeout 40 --max-requests 1000 --max-requests-jitter 200 --no-control-socket --log-config-json gunicorn-logging.json
fi

exec $TRACE "$@"
