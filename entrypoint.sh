#!/bin/sh
set -e

python manage.py collectstatic --no-input

# migrate is NOT run here — it's a separate one-shot `migrate` service in
# docker-compose.yml that web/poller depend on completing first. Running it
# per-service-start (the old behavior) raced when web and poller started
# together: a non-idempotent migration (e.g. one registering a TimescaleDB
# background job) could execute concurrently from both before either
# committed it as applied, producing duplicate side effects (see TODO.md).

if [ "$#" -eq 0 ]; then
    # gthread, not plain sync workers: this workload is almost entirely
    # I/O-bound (waiting on Postgres), and a single dashboard page load
    # fires ~10 parallel API calls (dashboard.js's Promise.all) — with only
    # 2 sync workers, one page load alone can saturate both and queue every
    # other request behind it (observed: a trivial ~50ms query taking 38-158s
    # wall-clock under concurrent dashboard load, all queueing, not query
    # cost). Threads share a worker's memory instead of each duplicating the
    # full Django import footprint, so 2 workers x 8 threads = 16 concurrent
    # request slots costs only modestly more than today's 2 plain workers,
    # not ~8x more the way reaching 16 via --workers 16 would. Postgres
    # max_connections=100 comfortably covers the worst case (confirmed).
    # --timeout 40: must stay comfortably above DB_STATEMENT_TIMEOUT_MS
    # (docker-compose.yml, 30s) so the DB cancels a slow query cleanly
    # before gunicorn would SIGKILL the worker out from under it.
    exec ddtrace-run gunicorn osm_changeset_api.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --worker-class gthread --workers 2 --threads 8 --timeout 40
fi

exec ddtrace-run "$@"
