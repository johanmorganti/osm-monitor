# Architecture

How this actually works, for anyone (human or AI agent) picking this up cold. For *why specific
decisions were made*, see [`decisions/`](decisions/README.md). For
open issues and deferred work, see [`../TODO.md`](../TODO.md). This doc explains the system as it
stands; it doesn't track day-to-day changes.

## Pages and endpoints

- `/` → `DashboardView` (Overview: map, changeset activity, rankings)
- `/objects/` → `ObjectsView` (objects changed, changeset sizes, largest/widest changesets, rankings by objects incl. hashtags)
- `/editors/` → `EditorsView` (one column per top-10 editor family for the selected range — default last year — plus an "Other editor families" column; each column has its own version drill-down toplist via `dimension=editor_version` and its own volume-over-time graph)
- `/changeset_import/` → `APILandingPageView` (poller status page — live catch-up batch progress)
- `/api/changesets/` → `ChangesetQueryView` (raw changeset records, filterable; defaults to the last 24 hours, no unfiltered "everything" mode)
- `/api/changesets/timeseries/` → `TimeseriesView` (volume over time, optionally grouped, `metric=count|objects`)
- `/api/changesets/summary/` → `SummaryView` (total_changesets/total_objects/avg_objects)
- `/api/changesets/toplist/` → `ToplistView` (top N by dimension × metric)
- `/api/changesets/geo/` → `GeoView` (changeset density per grid cell; `resolution=coarse|fine`, the latter viewport-scoped via `bbox`; each cell carries its geohash as `cell`), `/api/changesets/geo/cell/` → `GeoCellView` (the changesets of one cell, newest first, paginated — the Overview map's click-to-list panel; ClickHouse only)
- `/api/changesets/distribution/` → `DistributionView` (changeset-size histogram, exact percentiles, largest 1%'s share of objects), `/api/changesets/distribution/breakdown/` → `SizeBreakdownView` (size quartiles `by=` a dimension, `experience` or `day`), `/api/changesets/largest/` → `LargestView` (largest changesets `by=objects|area`) — ClickHouse only, 501 on Timescale
- `/api/docs/` → Swagger UI (drf-spectacular), `/api/schema/` the raw OpenAPI schema — the authoritative API reference

The aggregate endpoints default to the last 7 days when no dates are given. Static files are
served by Django itself in `DEBUG` mode (`django.conf.urls.static`).

Ingestion: `manage.py poll_sequences` (continuous poller; first run takes `--start <seq>` or the
`INITIAL_SEQUENCE` env var) and `manage.py import_from_dump` (bulk planet dump) are the only two
paths. A one-shot HTTP-triggered import (`ChangesetListView`/`ImportJobView`) was removed
2026-09-19: it ran on a bare `threading.Thread` inside the `web` worker with no resume/recovery and
had no real usage.

## Data flow

```
planet.osm.org (minutely replication)  ──┐                      ┌──> ClickHouse `changesets` ──> daily_rollup, filter_values
                                          ├──> parse + locate ───┤        (primary, serves the API)    (refreshed daily)
OSM full changesets dump (bulk import) ──┘   (osm_fetcher,       └──> Postgres changesets_changeset ──> cagg_*, FilterValue
                                               ingest/locate.py)        (TimescaleDB, deprecated copy)

API (timeseries/summary/toplist/geo/changesets) ──> analytics backend (ClickHouse by default) ──> dashboard.js
```

Two independent processes ingest changesets and safely converge:

- **`manage.py poll_sequences`** (the `poller` service) tails OSM's *minutely* replication feed —
  one small XML file per minute of real-world OSM activity. This is how the app stays up to date
  in normal operation. It can also walk *backward* from where it started for a bounded number of
  days (`--backfill-days`) to fill in recent history, then retires that backward walk
  automatically once it reaches the floor and continues live-only.
- **`manage.py import_from_dump`** streams OSM's full changesets planet dump (the whole history
  since 2005, as one very large XML file, optionally bz2-compressed in multiple concatenated
  streams — `import_from_dump.py`'s `_MultiStreamBz2Reader` handles that transparently). Use this
  for bulk historical backfill — it's dramatically faster than replaying years of minutely diffs
  through the poller.

Both paths funnel through the same batch logic in `osm_fetcher.py` (`import_changeset_batch`):
parse once, compute geohash + country in Python (`changesets/ingest/locate.py`), then hand the
records to every writer in `INGEST_BACKENDS` (`changesets/ingest/writers/`). Each writer is an
idempotent upsert by `changeset_id` (a changeset is replaced only when its `changes_count` grew):
ClickHouse just inserts and lets `ReplacingMergeTree` keep the latest version (queries read with
`FINAL`); the Timescale writer checks which ids already exist and inserts only the new or grown
ones. So the two paths — and a retried batch — overlap harmlessly instead of double-counting.

The rest of this document describes the **TimescaleDB backend, which is deprecated** (see
`TODO.md`'s "Phase out TimescaleDB"): it still runs and still receives every changeset, but the API
reads ClickHouse by default. For the ClickHouse side, see
[`decisions/analytics-backends.md`](decisions/analytics-backends.md) and the docstring of `changesets/analytics/clickhouse/backend.py`.

## Why a hypertable

Nearly all real query traffic here is time-filtered — the dashboard's default views, the
`timeseries`/`summary`/`toplist` API endpoints, `/api/changesets/`'s 24h-default list. A plain
Postgres table has no way to skip irrelevant data for those queries beyond a btree index scan
across the *whole* table. `changesets_changeset` is a TimescaleDB hypertable, partitioned by
`created_at` into monthly chunks (`changesets/migrations/0018_timescale_hypertable.py`), so a
query bounded to a week only opens the 1-2 chunks that could contain matching rows.

**The one real schema wrinkle**: TimescaleDB requires every UNIQUE constraint on a hypertable
(including the primary key) to include the partitioning column. `Changeset.id` is still Django's
`pk` for ORM purposes (`.get()`, `.filter(pk=...)`, etc. all work normally), but it's **not**
physically enforced unique by Postgres anymore — the PK constraint was dropped as part of the
hypertable conversion, replaced by a plain (non-unique) index for lookup performance. Real
duplicate-import protection is `UNIQUE (changeset_id, created_at)` instead, which does satisfy
Timescale's rule. `id` values stay practically unique regardless (never-reused sequence); this is
the standard, documented trade-off for using an ORM built around single-column PKs with
TimescaleDB.

Queries and Django ORM code are otherwise unaffected — TimescaleDB is a Postgres extension, not a
different query interface. `changesets/rollups.py`'s raw SQL and every `Changeset.objects...`
query in the Timescale backend work exactly as they would against a plain table.

## Aggregates: precomputed via TimescaleDB continuous aggregates, not query-time aggregation

The dashboard's *unfiltered* view, and any query filtered on exactly one of
contributor/editor/imagery/language, reads from TimescaleDB continuous aggregates (`cagg_*`
materialized views — migration `0020` onward) instead of aggregating the raw table on every
request. There's one CAgg per (dimension × grain): `cagg_volume_{hourly,daily}` for plain volume,
and `cagg_{editor,imagery,locale,contributor}_{hourly,daily}` for per-dimension breakdowns — see
`changesets/models.py`'s `Cagg*` classes (all `managed=False`; TimescaleDB owns their schema and
refresh, Django only maps onto them for querying) and `changesets/analytics/timescale/caggs.py`'s `CAGG_MODELS` /
`CAGG_MODELS_HOURLY` mapping.

A query filtered by *two or more* of those dimensions at once (e.g. a toplist filtered by
`imagery`, grouped by `editor`) has no matching CAgg — each one only tracks its own single
dimension — and falls back to querying `Changeset` directly (`changesets/analytics/timescale/backend.py`'s
`filtered_changesets`). This is a real, currently-unsolved performance cliff for that specific
query shape; see `docs/todo/continuous-aggregates-migration.md`.

CAggs refresh themselves via TimescaleDB's own background job scheduler
(`add_continuous_aggregate_policy`, registered per-CAgg in each migration) — no application code
triggers this. Each policy's `start_offset` (7 days on every CAgg here) assumes normal live
polling, where the only thing that can change after a row is inserted is `changes_count` growing
while the changeset is still open (bounded by OSM's 24h max open time) — see
[`decisions/old-dated-rows.md`](decisions/old-dated-rows.md) before assuming this window is too
narrow or too wide. A deliberate bulk/backward import lands outside that window and needs an
explicit `CALL refresh_continuous_aggregate(<view>, <lo>, <hi>)` — not yet automated for
`import_from_dump.py`, see `docs/todo/continuous-aggregates-migration.md`.

**`FilterValue`** (distinct known contributor/editor/imagery values, globally deduplicated — no
date dimension) backs the dashboard's autocomplete inputs. Populated incrementally by
`changesets/rollups.py`'s `refresh_filter_values_incremental()`, called from the poller's own loop
every ~120s (watermarked on `created_at` — the hypertable's partitioning column, so this gets real
chunk exclusion), and backfilled once for pre-existing data via `manage.py
backfill_filter_values`. Its size tracks the number of distinct values ever seen, not the number
of changesets, so it stays small (editor/imagery) or slow-growing (contributor) regardless of how
much history is imported — unlike querying `Changeset` directly for autocomplete, which would be
a full-table scan on every keystroke.

**Not live, but still on disk**: `DailyVolume`/`DailyBreakdown` tables and three rollup functions
in `changesets/rollups.py` predate the CAgg design above and nothing reads them anymore —
`refresh_rollups()` remains callable only as a manual escape hatch (`manage.py refresh_rollups`).
Dropping them outright is a pending follow-up migration — see
`docs/todo/continuous-aggregates-migration.md`.

## API design

`TimeseriesView` / `SummaryView` / `ToplistView` (`changesets/api/views.py`) replaced an earlier
single bundled stats endpoint. They're split by resource *shape*, not business concept —
analogous to a metrics platform's widget types:

- **`timeseries`**: anything date-bucketed. `group_by` omitted = plain hourly volume;
  `group_by=editor|imagery|locale|contributor` = daily volume as up to N per-name series (the
  top N by total count over the range).
- **`toplist`**: any ranked list. `dimension` (contributor/editor/imagery/locale) ×
  `metric` (count/objects) × `limit` (default 20). Combinations the old bundled endpoint never
  exposed (e.g. contributor × count) are just other parameter values now, not new code.
- **`summary`**: the handful of single-number KPIs for a range.

All three share filter-resolution and queryset-building helpers (`resolve_filters`,
`filtered_changesets`, `DIMENSION_FIELDS`) rather than duplicating that logic per view.
`dashboard.js` fetches all of what it needs in parallel (`Promise.all`) — more requests than the
old bundled endpoint, but each is small and independently cacheable, and total load time is
bounded by the slowest request rather than their sum.

Every DRF view carries `@extend_schema` annotations (drf-spectacular), so `/api/docs/` stays
accurate as endpoints change — that page (or `/api/schema/` for the raw OpenAPI document) is the
authoritative reference, not this file or the README.

## Observability

Datadog is optional. The base `docker-compose.yml` runs without it (tracing off); the
`docker-compose.datadog.yml` override adds everything below, enabled by setting
`COMPOSE_FILE=docker-compose.yml:docker-compose.datadog.yml` in `.env` along with `DD_API_KEY`
and `DD_POSTGRES_PASSWORD`.

- **Agent**: the override adds a `datadog-agent` compose service. `web`/`poller` reach it over
  unix sockets in the shared `datadog-sockets` volume (`apm.socket`, `dsd.socket`); it discovers
  containers and tails their logs via the docker socket, and picks up the Postgres check from the
  `db` service's `com.datadoghq.ad.checks` label.
- **APM + structured logs**: with tracing enabled, `entrypoint.sh` wraps both `web` (gunicorn) and
  `poller` in `ddtrace-run`; `osm_changeset_api/logging_json.py` emits structured JSON logs with
  `dd.trace_id`/`dd.span_id` injected (`DD_LOGS_INJECTION=true`), so a log line and the trace it
  happened during are correlated in Datadog.
- **Database Monitoring**: the `db` service always preloads `pg_stat_statements` alongside
  `timescaledb` (`shared_preload_libraries`), and `db/init/01-datadog.sh` always creates the
  extension, so query-level stats are available with or without Datadog. When
  `DD_POSTGRES_PASSWORD` is set, the same script also creates the low-privilege `datadog` role and
  a `datadog` schema with a `SECURITY DEFINER` `explain_statement()` function, so the agent can
  request `EXPLAIN` plans without broader query access (fresh data directory only; on an existing
  one, run the script once by hand). The override's postgres check has `dbm: true`.
- **Resource limits**: every service has a `mem_limit`, and `db` a non-default
  `effective_cache_size` (`docker-compose.yml`), so one heavy query/index build/import can't
  starve everything else running alongside it. If DBM's query collection ever
  needs to be paused during a heavy bulk operation (it polls frequently and will contend for I/O
  under load), the clean way is `REVOKE CONNECT ON DATABASE ... FROM datadog;` (and `GRANT` it
  back after) — no service restart required, unlike disabling the check via its Docker label.

## Deployment

`docker-compose.yml` defines three services: `db` (TimescaleDB), `web` (gunicorn, single worker —
see `TODO.md` for why that's a known limitation), `poller` (the continuous ingester);
`docker-compose.datadog.yml` optionally adds `datadog-agent` (see Observability). `deploy.sh`
stamps the build with the current git commit (`GIT_VERSION`, used as Datadog's `DD_VERSION` tag
when enabled), then `docker compose build && up -d`. Migrations and static files are handled by `entrypoint.sh` on
every container start.

`db/init/`'s scripts only run automatically on a genuinely fresh Postgres data directory
(the official image's behavior) — recreating `db` against an *existing* volume skips them, so a
schema/extension change that needs to apply to a running system still needs a manual one-time
step (each script's own comments say what).
