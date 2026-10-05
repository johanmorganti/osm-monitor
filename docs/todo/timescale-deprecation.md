# Phase out TimescaleDB (keep Postgres)

ClickHouse is the default analytics backend and the primary ingest writer (2026-10-02). TimescaleDB
is deprecated: it still works (`ANALYTICS_BACKEND=timescale`, the `X-Analytics-Backend` override,
`check_backend_parity`) and still receives a copy of every changeset (`INGEST_BACKENDS` default
`clickhouse,timescale`), so it stays a live fallback and comparison point while it's retired step by
step. **Postgres itself stays**: Django's own tables (sessions, admin, auth) and the poller's
`SequenceState` live there, independently of where changesets are stored.

## Why gradual

The Timescale side holds the only second copy of the data, and the parity checker needs both
backends populated to mean anything. Each step below is reversible until the last one; stop writing
only once nothing reads it any more.

## Steps

1. **Done** — ClickHouse in the base `docker-compose.yml`, defaults switched in `settings.py`,
   Timescale demoted to second writer.
2. **Stop serving from Timescale's maintenance-heavy parts**: nothing reads the CAggs unless
   `ANALYTICS_BACKEND=timescale` or the override asks for it, but their refresh policies still run.
   Decide whether the fallback is still wanted; if not, pause the CAgg policies (and compression)
   to free the host's I/O.
3. **Stop writing**: drop `timescale` from the `INGEST_BACKENDS` default. From then on the
   Timescale copy goes stale, so the backend, the override value and the parity checker's
   Timescale side should go in the same change (`changesets/analytics/timescale/`,
   `changesets/ingest/writers/timescale.py`, the `timescale` entries in both registries).
4. **Drop the schema** in a migration: the CAggs, the `changesets_changeset` hypertable and its
   trigger, `FilterValue` (ClickHouse's `filter_values` replaces it), the old rollup tables
   (`DailyVolume`/`DailyBreakdown`, 415 MB, unread since the CAggs; `changesets/rollups.py`,
   `refresh_rollups`, `RollupState`), the unused `changesets_changeset_id_idx` (758 MB), and the
   Timescale-only management commands (`refresh_caggs`, `backfill_*` CAgg/FilterValue commands,
   `recompute_locations`).
5. **Swap the image**: `timescale/timescaledb-ha` → plain `postgres` once no migration needs the
   `timescaledb`/`postgis` extensions any more (old migrations that create them need squashing or a
   guard first). PostGIS is already unused at ingest — country lookup is
   `changesets/ingest/locate.py` against `changesets/data/country_boundaries.geojson` — but
   `country_boundaries` and its migrations still exist.
6. Clean up docs: `docs/decisions/timescale-storage.md` and
   `docs/ARCHITECTURE.md` become history (git), not live guidance.

## History

The CAgg work this retires (pair CAggs, refresh crashes and stalls, the old rollups' cost: ~15% of
DB time for output nothing read, the filtered-query cliff) was tracked in
`docs/todo/continuous-aggregates-migration.md`, removed 2026-10-05 as moot once TimescaleDB goes;
it's in git history (last version: commit 053badf). Code comments that cite its measurements point here.

## Things that still touch the Postgres `Changeset` model

`changesets/admin.py`, `changesets/serializers.py` (shared field list for the raw API — the
ClickHouse `_Record` is serialized through it), the `backfill_*` commands and
`recompute_locations`. Check the serializer before step 4: it must stop depending on the model.
