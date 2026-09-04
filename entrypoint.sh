#!/bin/sh
set -e

python manage.py migrate --no-input
python manage.py collectstatic --no-input

if [ "$#" -eq 0 ]; then
    exec ddtrace-run gunicorn osm_changeset_api.wsgi:application --bind "0.0.0.0:${PORT:-8000}"
fi

exec ddtrace-run "$@"
