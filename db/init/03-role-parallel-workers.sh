#!/bin/bash
# Default the app role to max_parallel_workers_per_gather = 0, closing a gap
# in the existing per-script fix: cagg_maintenance.refresh_caggs_over_range
# already sets this on its own connection before calling
# CALL refresh_continuous_aggregate(...) (see that function's docstring —
# a parallel worker's dynamic shared memory segment blowing past the db
# container's shm_size crashes the whole postmaster, not just the one query,
# forcing a multi-second-to-multi-minute WAL redo on every reconnect — see
# CLAUDE.md's "Geo storage" section, lesson 2). That protection only covers
# code that explicitly opts in, though — every CAgg's own automatic
# add_continuous_aggregate_policy background refresh job runs as this same
# role but through TimescaleDB's internal scheduler, never through
# cagg_maintenance.py, so it never got the same guard. Confirmed as a real,
# independent trigger (2026-09-22): the postmaster crash-restarted while no
# manual backfill/migration was running at all, immediately after several
# new CAggs (and their background policies) had just been added the same
# session — the growing number of scheduled policy refreshes appears to
# raise how often *something* hits a big-enough chunk to trip this,
# independent of any explicit backfill.
#
# Role-level rather than a query-level SET so it's inherited by every
# connection under this role, including TimescaleDB's own background
# workers — no call site can forget it. Only runs automatically on a
# genuinely fresh data directory (same init-script caveat as 01/02) — on an
# existing volume, apply the ALTER ROLE directly once.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    ALTER ROLE "$POSTGRES_USER" SET max_parallel_workers_per_gather = 0;
EOSQL
