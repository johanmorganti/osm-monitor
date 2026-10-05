# TimescaleDB and Postgres removed; SQLite holds the app's state (2026-10-05)

ClickHouse became the default analytics backend and primary writer on 2026-10-02
([analytics-backends.md](analytics-backends.md)). TimescaleDB was kept as a deprecated second
copy and fallback until 2026-10-05, then removed along with Postgres itself.

## Why

- **Nothing read it.** The API, the dashboard, the object tables and every new feature were
  ClickHouse only; TimescaleDB only received writes and ran its own continuous-aggregate and
  compression jobs.
- **It cost the most to run.** 53 GB on disk (a 20 GB compressed hypertable plus 23 continuous
  aggregates), a 3 GB memory reservation, and a history of crashes (`/dev/shm` exhaustion during
  CAgg refreshes, out-of-memory kills). The last one happened during the removal itself: a
  year-by-year `count(*)` over the hypertable took over 2 GB in one backend, the kernel killed it,
  and Postgres went through crash recovery.
- **What was left was tiny.** After the changesets, the app's own state is two rows (the pollers'
  positions) and Django's tables, a few MB.

## How

1. **Checked ClickHouse holds everything:** it and the TimescaleDB daily continuous aggregate both
   counted 189,925,365 changesets up to 2026-10-03, every year equal.
2. **Stopped writing and reading** (`INGEST_BACKENDS=clickhouse`): deleted the TimescaleDB
   backend and writer, the old rollups, `cagg_maintenance.py`, the CAgg / FilterValue / backfill
   commands and `load_country_boundaries`; `check_backend_parity` now compares `clickhouse_raw`
   with `clickhouse`, `recompute_locations` reads and fixes ClickHouse, and the raw API's
   serializer no longer uses the model. Proven neutral: 16 fixed API calls byte-identical, the raw
   changeset list identical apart from the host in `next`, the OpenAPI schema only losing the
   Postgres column bounds.
3. **Moved the state to SQLite** (`SQLITE_PATH`, a file in `SQLITE_DIR` on the host data directory,
   shared by `migrate`, `web` and both pollers). The pollers were stopped, their rows copied, and
   they resumed from the same positions. The migration history was reset to one `0001_initial`
   (`SequenceState`, `DiffState`); the 58 old migrations are in git history.
4. **Deleted** the `db` service, `db/init/`, `psycopg2`, `dj-database-url`, the Datadog Postgres
   check, and the 56 GB Postgres data directory, without a backup: ClickHouse has every changeset,
   and OSM's changeset dump can rebuild them.

## SQLite on a shared folder

Under Colima the SQLite file lives on a folder shared into the VM (virtiofs), and three
containers write to it. SQLite relies on file locking, which can be unreliable on shared
filesystems, so it was tested first: 3 containers × 3,000 interleaved writes in WAL mode, no
error, every final value present, `PRAGMA integrity_check` ok. `changesets/apps.py` sets WAL and
`synchronous=NORMAL` on every connection; Django waits up to 30 s for another writer's lock. The
fallback, had it failed, was a Docker volume inside the VM.

## Consequences

- No more `statement_timeout`: the rule that long commands opt out of it
  ([statement-timeout.md](statement-timeout.md)) is history. ClickHouse queries from the API are
  capped by `max_execution_time` (30 s).
- [timescale-storage.md](timescale-storage.md), the TimescaleDB parts of
  [old-dated-rows.md](old-dated-rows.md), [none-bucket.md](none-bucket.md),
  [geo-geohash.md](geo-geohash.md) and [analytics-backends.md](analytics-backends.md) describe the removed design; they stay as history.
- The deleted files stayed held by Colima's virtualization process (it keeps a handle on every
  file the VM has touched), so the disk space comes back only when Colima restarts.
