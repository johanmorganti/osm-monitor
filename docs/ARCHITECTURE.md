# Architecture

How this actually works, for anyone (human or AI agent) picking this up cold. For *why specific
decisions were made* (in the terse, decision-log sense), see [`../CLAUDE.md`](../CLAUDE.md). For
open issues and deferred work, see [`../TODO.md`](../TODO.md). This doc explains the system as it
stands; it doesn't track day-to-day changes.

## Data flow

```
planet.osm.org (minutely replication)  ──┐
                                          ├──> changesets_changeset (TimescaleDB hypertable)
OSM full changesets dump (bulk import) ──┘             │
                                                         ├──> DailyVolume / DailyBreakdown (rollups)
                                                         ├──> FilterValue (autocomplete)
                                                         │
                                          API (timeseries/summary/toplist/changesets) ──> dashboard.js
```

Two independent processes write to the same table and safely converge:

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

Both paths funnel through the same batched insert logic in `osm_fetcher.py`
(`import_changeset_batch`): for each batch, one query checks which `changeset_id`s already exist,
then only the new ones get inserted. This makes the two paths idempotent with respect to each
other — running a bulk import that overlaps data the poller already ingested (or vice versa) just
skips the overlap cheaply, rather than erroring or double-counting.

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
query in `views.py` work exactly as they would against a plain table.

## Rollups: precomputed aggregates, not query-time aggregation

The dashboard's *unfiltered* view (no contributor/editor/imagery filter — the common case) reads
from two small precomputed tables instead of aggregating the raw table on every request:

- **`DailyVolume`** (`date`, `hour`, `count`, `changes_sum`) — per-hour changeset volume.
- **`DailyBreakdown`** (`date`, `category`, `name`, `count`, `changes_sum`) — per-day counts by
  editor/imagery/locale/contributor.

As soon as any contributor/editor/imagery filter is applied, the API falls back to querying
`Changeset` directly (`changesets/views.py`'s `_filtered_changesets`) — the rollups don't carry
those dimensions, and a filtered result set is usually a small enough slice of the table (helped
by the hypertable's partition pruning plus expression indexes on `UPPER(user)` /
`UPPER(created_by_family)` / `UPPER(imagery_family)`, since filters are case-insensitive) that
this stays fast without needing a rollup per filter combination.

Rollups are kept fresh by `changesets/rollups.py`, called from the poller's own loop on two
cadences:

- **`refresh_rollups_incremental()`** (every ~2 minutes): merges only `Changeset` rows inserted
  since the last call (tracked via `RollupState.last_id`, an index range scan regardless of table
  size) into the existing rollup rows with an UPSERT.
- **`refresh_rollups_reconcile()`** (every ~6 hours): corrects drift the incremental path can
  leave behind (`osm_fetcher.py` sometimes deletes and recreates a changeset that grew more edits
  before closing — the recreated row's contribution gets added correctly, but the deleted row's
  old contribution can briefly linger). Rather than recomputing from the *entire* table like the
  original design did (prohibitively slow once the table is large — see `TODO.md`'s history),
  this only recomputes the last few days: OSM changesets close within at most a few days in
  practice, so drift can't exist further back than that.

`refresh_rollups()` (the original full-table rebuild) still exists as a manual escape hatch
(`manage.py refresh_rollups`) for when something broader than the recent window needs correcting
— not called automatically.

**`FilterValue`** (distinct known contributor/editor/imagery values, globally deduplicated — no
date dimension) backs the dashboard's autocomplete inputs the same way: populated incrementally
alongside the rollups, backfilled once for pre-existing data via
`manage.py backfill_filter_values`. Its size tracks the number of distinct values ever seen, not
the number of changesets, so it stays small (editor/imagery) or slow-growing (contributor)
regardless of how much history is imported — unlike querying `Changeset` directly for
autocomplete, which would be a full-table scan on every keystroke.

## API design

`TimeseriesView` / `SummaryView` / `ToplistView` (`changesets/views.py`) replaced an earlier
single bundled stats endpoint. They're split by resource *shape*, not business concept —
analogous to a metrics platform's widget types:

- **`timeseries`**: anything date-bucketed. `group_by` omitted = plain hourly volume;
  `group_by=editor|imagery|locale|contributor` = daily volume as up to N per-name series (the
  top N by total count over the range).
- **`toplist`**: any ranked list. `dimension` (contributor/editor/imagery/locale) ×
  `metric` (count/objects) × `limit` (default 20). Combinations the old bundled endpoint never
  exposed (e.g. contributor × count) are just other parameter values now, not new code.
- **`summary`**: the handful of single-number KPIs for a range.

All three share filter-resolution and queryset-building helpers (`_resolve_range_and_filters`,
`_filtered_changesets`, `DIMENSION_FIELDS`) rather than duplicating that logic per view.
`dashboard.js` fetches all of what it needs in parallel (`Promise.all`) — more requests than the
old bundled endpoint, but each is small and independently cacheable, and total load time is
bounded by the slowest request rather than their sum.

Every DRF view carries `@extend_schema` annotations (drf-spectacular), so `/api/docs/` stays
accurate as endpoints change — that page (or `/api/schema/` for the raw OpenAPI document) is the
authoritative reference, not this file or the README.

## Observability

- **APM + structured logs**: `ddtrace-run` wraps both `web` (gunicorn) and `poller` (see
  `entrypoint.sh`); `osm_changeset_api/logging_json.py` emits structured JSON logs with
  `dd.trace_id`/`dd.span_id` injected (`DD_LOGS_INJECTION=true`), so a log line and the trace it
  happened during are correlated in Datadog.
- **Database Monitoring**: the `db` service preloads `pg_stat_statements` alongside `timescaledb`
  (`shared_preload_libraries`) and the postgres Datadog check has `dbm: true`
  (`docker-compose.yml`'s `com.datadoghq.ad.checks` label). Execution-plan collection additionally
  needs a dedicated `datadog` schema with a `SECURITY DEFINER` `explain_statement()` function, so
  the low-privilege `datadog` role can request `EXPLAIN` plans without broader query access — see
  `db/init/01-datadog-dbm.sql`, which sets this up automatically on a fresh database (existing
  volumes need the role created and that SQL applied manually once, per that file's own
  comments).
- **Host constraints matter here**: this stack has historically run on a memory/IO-constrained
  host shared with unrelated apps. `mem_limit`/`mem_reservation` on every service and a
  non-default `effective_cache_size` (`docker-compose.yml`) exist specifically so one heavy
  query/index build/import can't starve the other apps on the box. If DBM's query collection ever
  needs to be paused during a heavy bulk operation (it polls frequently and will contend for I/O
  under load), the clean way is `REVOKE CONNECT ON DATABASE ... FROM datadog;` (and `GRANT` it
  back after) — no service restart required, unlike disabling the check via its Docker label.

## Deployment

`docker-compose.yml` defines three services: `db` (TimescaleDB), `web` (gunicorn, single worker —
see `TODO.md` for why that's a known limitation), `poller` (the continuous ingester). `deploy.sh`
stamps the build with the current git commit as the `DD_VERSION` tag, then
`docker compose build && up -d`. Migrations and static files are handled by `entrypoint.sh` on
every container start.

`db/init/`'s SQL scripts only run automatically on a genuinely fresh Postgres data directory
(the official image's behavior) — recreating `db` against an *existing* volume skips them, so a
schema/extension change that needs to apply to a running system still needs a manual one-time
step (each script's own comments say what).
