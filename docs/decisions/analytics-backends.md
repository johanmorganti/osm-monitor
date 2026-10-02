# Analytics backends: the API asks, a backend answers (2026-10-01)

The public JSON API (`changesets/api/`) is backend-agnostic: each view parses and validates its
parameters, applies the API-level rules (date defaults, `pick_interval`, geohash precision per
viewport, response shapes, error messages) and asks the configured analytics backend
(`changesets/analytics/`, selected by `ANALYTICS_BACKEND`, default `clickhouse`) for the data.
The contract is `changesets/analytics/base.py`: a `Filters` value object plus `summary`,
`timeseries`, `toplist`, `geo_cells`, `changesets`, `autocomplete`, each returning plain data
shaped like the JSON response.

**Why:** to run other databases (ClickHouse first) behind the exact same endpoints and compare
them byte for byte, then keep whichever runs better on this machine. The interface sits at the
level of operations, not SQL, so each backend stays idiomatic: the Timescale backend routes
between CAggs, pair CAggs and the raw hypertable (`changesets/analytics/timescale/`); another
backend may simply scan its raw table.

Backends today: `clickhouse` (default) and `timescale` (deprecated, see below and [timescale-storage.md](timescale-storage.md)). ClickHouse
(`changesets/analytics/clickhouse/`: SQL over
the raw `changesets` table with `FINAL`, plus one daily rollup; `clickhouse_raw` skips the rollup, for
measurement). The ClickHouse rollups are **refreshable** materialized views (`schema/0002_rollups.sql`:
`daily_rollup`, every dimension in one ~4M-row table, and `filter_values` for autocomplete),
recomputed daily from `FINAL` data, never incremental ones: `changesets` receives new versions of
existing changesets (grown, or resurfaced through a comment), which an incremental view would count
twice. Queries read the rollup up to its last covered day and the raw table after it, in one
`UNION ALL`. Verified exact: 200/200 parity cases identical with and without the rollup.
The unfiltered map has two more (80/80 random map cases identical with and without them) (`schema/0003_geo_rollups.sql`, 2026-10-02): `geo_coarse_daily`
(3-character cells by day, ~10.7M rows, ordered by day) for the world view, and `geo_cells_daily`
(full geohash by day, ~117M rows, **ordered by cell**) for viewports, which read only the cells
covering them (`geohash_bbox_cover` in `changesets/geo.py`). The raw table is ordered by time, so
before these a full-history map read every geohash: world 9-20 s → 0.6-0.9 s, Paris 30 s → 0.2 s,
France 13 s → 2 s, all byte-identical to the raw path. Ordering by cell has one cost: a
country-sized viewport over a short range is faster on the raw table (France, 3 months: 1.56 s vs
0.36 s), so `_geo_rows` routes that one case to raw — re-measure before changing that rule.
The ClickHouse backend reproduces Timescale's observable semantics, including its path-dependent
NULL grouping: aggregate-shaped questions put untagged imagery/language/country in `NONE_BUCKET`,
raw-table ones leave NULL names out (see that module's docstring for the few deliberate
differences). Requests can pick a backend internally with `X-Analytics-Backend` +
`X-Analytics-Token` (`ANALYTICS_OVERRIDE_TOKEN`; unset = disabled), which is how benchmarks and
parity checks hit every backend through the same public endpoints.

Ingestion mirrors this on the write side: the poller and `import_from_dump` parse each batch
once (`osm_fetcher`, then `ingest/locate.py`) and hand the same records to every writer listed in
`INGEST_BACKENDS` (default `clickhouse,timescale`; `changesets/ingest/writers/`). A writer upserts by
`changeset_id` (replace only when `changes_count` grew) and is idempotent, so a batch that failed on
any writer is simply retried on all of them; `after_backfill(start, end)` is its post-bulk-import
maintenance (Timescale: refresh CAggs + FilterValue). Verified with a differential test: 3 real
replication sequences imported inside a rolled-back transaction, with rows pre-arranged to hit the
create, update and skip paths, giving identical counts and rows before and after the refactor.

**ClickHouse became the default (2026-10-02); TimescaleDB is deprecated, Postgres stays.** Measured
over full history on the same host, with byte-identical answers: ~3.7x less disk (no CAggs needed),
roughly 3x less total time over the 46-call benchmark, no timeouts where Timescale hit its 30s cap,
and post-import maintenance in minutes instead of hours. Timescale keeps working and keeps receiving
a copy of every changeset (second writer) while it's retired step by step — see `TODO.md`'s
"Phase out TimescaleDB" entry. Postgres itself is not going away: Django's own tables and
`SequenceState` live there. So: new analytics features go into the ClickHouse backend first; don't
add new CAggs or Timescale-only features, and a Timescale implementation of a new backend method
is optional (raise `NotImplementedError` rather than building new CAggs for it). [timescale-storage.md](timescale-storage.md)
and `docs/ARCHITECTURE.md`'s CAgg sections describe the deprecated backend.

**How to apply:** behavior that defines the public API goes in `changesets/api/`, never in a
backend; how a backend gets the numbers stays inside that backend. The refactor that introduced
this split was verified by snapshotting 46 fixed API calls before and after (all byte-identical)
and diffing the OpenAPI schema (identical). Do the same for any change meant to be
behavior-neutral.
