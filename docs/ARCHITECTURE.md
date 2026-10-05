# Architecture

How this actually works, for anyone (human or AI agent) picking this up cold. For *why specific
decisions were made*, see [`decisions/`](decisions/README.md). For
open issues and deferred work, see [`../TODO.md`](../TODO.md). This doc explains the system as it
stands; it doesn't track day-to-day changes.

## Pages and endpoints

- `/` → `DashboardView` (Overview: map, changeset activity, rankings)
- `/objects/` → `ObjectsView` (objects changed, changeset sizes, largest changesets, rankings by objects incl. hashtags)
- `/editors/` → `EditorsView` (one column per top-10 editor family for the selected range — default last year — plus an "Other editor families" column; each column has its own version drill-down toplist via `dimension=editor_version` and its own volume-over-time graph)
- `/changeset_import/` → `APILandingPageView` (poller status page — live catch-up batch progress)
- `/api/changesets/` → `ChangesetQueryView` (raw changeset records, filterable; defaults to the last 24 hours, no unfiltered "everything" mode)
- `/api/changesets/timeseries/` → `TimeseriesView` (volume over time, optionally grouped, `metric=count|objects`; with `metric=objects` also `group_by=action|object_type|feature`, from the object tables)
- `/api/changesets/summary/` → `SummaryView` (total_changesets/total_objects/avg_objects)
- `/api/changesets/toplist/` → `ToplistView` (top N by dimension × metric; with `metric=objects` also `dimension=action|object_type|feature`)
- `/api/changesets/geo/` → `GeoView` (changeset density per grid cell; `resolution=coarse|fine`, the latter viewport-scoped via `bbox`; each cell carries its geohash as `cell`), `/api/changesets/geo/cell/` → `GeoCellView` (the changesets of one cell, newest first, paginated — the Overview map's click-to-list panel)
- `/api/changesets/distribution/` → `DistributionView` (changeset-size histogram, exact percentiles, largest 1%'s share of objects), `/api/changesets/distribution/breakdown/` → `SizeBreakdownView` (size quartiles `by=` a dimension or `day`), `/api/changesets/largest/` → `LargestView` (largest changesets `by=objects|area`)
- `/api/objects/most-edited/` → `MostEditedObjectsView` (objects with the most edits over the last 7 days, from `object_edits`; the Objects page's "Most Edited Objects last week")
- `/api/docs/` → Swagger UI (drf-spectacular), `/api/schema/` the raw OpenAPI schema — the authoritative API reference

The aggregate endpoints default to the last 7 days when no dates are given. Static files are
served by Django itself in `DEBUG` mode (`django.conf.urls.static`).

Ingestion: `manage.py poll_sequences` (continuous poller; first run takes `--start <seq>` or the
`INITIAL_SEQUENCE` env var) and `manage.py import_from_dump` (bulk planet dump) are the only two
changeset paths; `manage.py poll_diffs` ingests the objects themselves (see "Object changes"
below). A one-shot HTTP-triggered import (`ChangesetListView`/`ImportJobView`) was removed
2026-09-19: it ran on a bare `threading.Thread` inside the `web` worker with no resume/recovery and
had no real usage.

## Data flow

```
planet.osm.org (minutely replication)  ──┐
                                          ├──> parse + locate ──> ClickHouse `changesets` ──> daily_rollup, filter_values,
OSM full changesets dump (bulk import) ──┘   (osm_fetcher,          (serves the API)            map rollups (refreshed daily)
                                               ingest/locate.py)

planet.osm.org (minutely + daily osmChange diffs) ──> poll_diffs (ingest/osmchange.py, ingest/objects.py)
    ──> ClickHouse object_changes, object_change_features (all time), object_versions (92 days)

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
idempotent upsert by `changeset_id` (a changeset is replaced when its `changes_count` grew or it
closed): ClickHouse just inserts and lets `ReplacingMergeTree` keep the latest version (queries
read with `FINAL`). So the two paths — and a retried batch — overlap harmlessly instead of
double-counting.

The feed sometimes never publishes a changeset's closing update, so the poller also re-fetches,
hourly, the changesets still stored as open 25 h after creation from the OSM API
(`changesets/ingest/reconcile.py`, [`decisions/stale-open-changesets.md`](decisions/stale-open-changesets.md)).

### Object changes

**`manage.py poll_diffs`** (the `diff-poller` service) reads the replication *diffs*, which carry
every object version uploaded (with its changeset id, new tags and geometry), not just the
changeset's `changes_count`. Live, it follows the minutely diffs; behind that, it backfills the
last 92 days from the daily diffs, one day per round (a daily diff is exactly the minutely diffs
stamped within that day, so the two never overlap). It writes ClickHouse only: per-changeset
counts by type × action (`object_changes`) and by feature (`object_change_features`), kept for all
time, and every object version (`object_versions`), kept 92 days. Its position is `DiffState` in
SQLite. The API reads them through `object_daily_rollup` (schema/0005, built a day at a time, joined
to `changesets` for the day and the filters) for `group_by`/`dimension=action|object_type|feature`,
shown in the Objects page's "What was changed" section. Design, measurements and the checks against
`changes_count`: [`decisions/object-changes.md`](decisions/object-changes.md).

## Analytics: ClickHouse and its rollups

Every API question goes through the backend contract (`changesets/analytics/base.py`) to the
ClickHouse backend (`changesets/analytics/clickhouse/backend.py`; its docstring says which
questions read which rollup). The rollups are refreshable materialized views
(`changesets/analytics/clickhouse/schema/`), read up to their watermark with the raw table after
it: see [`decisions/analytics-backends.md`](decisions/analytics-backends.md). Until 2026-10-05 a
TimescaleDB copy in Postgres served the same API through continuous aggregates; it was removed,
see [`decisions/timescale-removal.md`](decisions/timescale-removal.md) (its design is in
[`decisions/timescale-storage.md`](decisions/timescale-storage.md) and in git history).

**App state** (the pollers' positions, Django's own tables) is a SQLite file (`SQLITE_PATH`),
shared by `web` and both pollers, in WAL mode.

## API design

`TimeseriesView` / `SummaryView` / `ToplistView` (`changesets/api/views.py`) replaced an earlier
single bundled stats endpoint. They're split by resource *shape*, not business concept —
analogous to a metrics platform's widget types:

- **`timeseries`**: anything date-bucketed. `group_by` omitted = plain volume;
  `group_by=contributor|editor|imagery|language|country|hashtag` (or, with `metric=objects`,
  `action|object_type|feature`) = up to 20 per-name series (the top 20 over the range).
- **`toplist`**: any ranked list. `dimension` × `metric` (count/objects) × `limit` (default 20).
  Combinations the old bundled endpoint never exposed (e.g. contributor × count) are just other
  parameter values now, not new code.
- **`summary`**: the handful of single-number KPIs for a range.

All three share the filter parsing (`changesets/api/params.py`: `resolve_filters`,
`pick_interval`) and the backend contract rather than duplicating that logic per view.
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
and `DD_CLICKHOUSE_PASSWORD`.

- **Agent**: the override adds a `datadog-agent` compose service. `web` and both pollers reach it
  over unix sockets in the shared `datadog-sockets` volume (`apm.socket`, `dsd.socket`); it
  discovers containers and tails their logs via the docker socket, and picks up the ClickHouse
  check from the `clickhouse` service's `com.datadoghq.ad.checks` label.
- **APM + structured logs**: with tracing enabled, `entrypoint.sh` wraps `web` (gunicorn) and
  the pollers in `ddtrace-run`; `osm_changeset_api/logging_json.py` emits structured JSON logs with
  `dd.trace_id`/`dd.span_id` injected (`DD_LOGS_INJECTION=true`), so a log line and the trace it
  happened during are correlated in Datadog.
- **Live Processes**: the agent shares the host's PID namespace (`pid: host`) and collects every
  process of the Docker VM, all containers' and the VM's own. Under Colima that's the Linux VM,
  not macOS: the Mac's own processes would need an agent installed natively.
- **Database Monitoring**: ClickHouse, through the `datadog` user `clickhouse_migrate` creates from
  `DD_CLICKHOUSE_PASSWORD` (read access to `system.*`, plus the app database so it can `EXPLAIN`
  the queries it captures).
- **Resource limits**: every service has a `mem_limit` (ClickHouse sizes its own memory budget
  from its own), so one heavy query or import can't starve everything else running alongside it.

## Deployment

`docker-compose.yml` defines five services, plus the SQLite file (`SQLITE_DIR`) that
`migrate`, `web` and both pollers share:

- `clickhouse`: the analytics store the API reads (changesets, object tables, rollups).
- `migrate`: one-shot, runs before the others start: Django migrations (SQLite),
  `clickhouse_migrate` (the ClickHouse schema and refreshable rollups). A separate service so two
  containers never run migrations concurrently.
- `web`: gunicorn, 2 `gthread` workers × 8 threads (`entrypoint.sh` has the measurement behind
  that number), behind ClickHouse's 30 s query cap.
- `poller`: `poll_sequences`, the changeset feed (plus the hourly re-fetch of changesets left
  open, `ingest/reconcile.py`).
- `diff-poller`: `poll_diffs`, the minutely/daily osmChange diffs into the object tables.

`docker-compose.datadog.yml` optionally adds `datadog-agent` (see Observability). `deploy.sh`
checks `.env`, stamps the build with the current git commit (`GIT_VERSION`, Datadog's
`DD_VERSION` tag when enabled), then runs `docker compose build && up -d`; `entrypoint.sh` only
collects static files and starts gunicorn (or the given command). Running it: `DEPLOYMENT.md`.
