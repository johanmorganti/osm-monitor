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
    # 2 workers: the single-worker default meant any one slow/heavy request
    # blocked every other request until gunicorn's 30s timeout killed it —
    # cheap to raise given a single worker only uses ~190MB against this
    # container's 512MB limit.
    exec ddtrace-run gunicorn osm_changeset_api.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2
fi

exec ddtrace-run "$@"
