#!/bin/bash
# Default the app role to a bounded statement_timeout, inverting the
# previous default of "unbounded unless told otherwise" to "bounded unless
# told otherwise" — found the hard way (2026-09-18) when a killed ad-hoc
# `docker compose exec db psql` client left its query running server-side
# for 2+ minutes, pegging I/O wait at 45% on this HDD with no client left
# to notice or cancel it. web already has its own tighter 30s cap
# (docker-compose.yml's DB_STATEMENT_TIMEOUT_MS, applied via connection
# OPTIONS) which overrides this role default for that service specifically;
# this is the floor for everything else — any interactive/admin psql
# session, and any management command that doesn't explicitly opt out.
#
# 120s, not something tighter: generous enough for an occasional legitimate
# slow analytic query against the raw table, still short enough that an
# abandoned session can't hold a seek-bound query open indefinitely.
#
# poll_sequences and the long-running management commands (import_from_dump,
# refresh_rollups, CAgg backfills) legitimately need unbounded queries —
# they opt out explicitly with `SET statement_timeout = 0`, not by being
# exempt from this default (see poll_sequences.py / management commands).
#
# Only runs automatically on a genuinely fresh data directory (same
# init-script caveat as 01-datadog-dbm.sql) — on an existing volume, apply
# the ALTER ROLE directly once.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    ALTER ROLE "$POSTGRES_USER" SET statement_timeout = '120s';
EOSQL
