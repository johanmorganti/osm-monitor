# CLAUDE.md — OSM Monitor

Project context and architectural decisions for AI-assisted development. See `TODO.md` for the
running backlog of known issues and deferred design work — check it before starting non-trivial
work, and keep it updated as things get fixed or newly deferred.

## Project overview

Django app that ingests OSM changeset replication sequences from planet.osm.org, stores them in a
database, and serves a Chart.js dashboard plus a REST API.

Key entry points:
- `/` → `DashboardView` (Chart.js dashboard)
- `/api/changesets/` → `ChangesetQueryView` (filterable REST API, raw changeset records)
- `/api/changesets/timeseries/` → `TimeseriesView` (volume over time, optionally grouped)
- `/api/changesets/summary/` → `SummaryView` (total_changesets/total_objects/avg_objects)
- `/api/changesets/toplist/` → `ToplistView` (top 20 by dimension × metric)
- `/api/sequence/<start>/<end>/` → `ChangesetListView` (one-shot import)
- `/api/docs/` → Swagger UI (drf-spectacular) for the public API, `/api/schema/` for the raw OpenAPI schema
- `/changeset_import/` → `APILandingPageView` (import helper UI)
- `manage.py poll_sequences` → continuous background poller

## Architecture decisions

### Template / JS separation, async data fetch
`dashboard.html` is a thin HTML shell only (no server-rendered chart data) — it renders
instantly and reads only `{{ request.GET.xxx }}` for filter-input defaults. All Chart.js
initialisation lives in `static/js/dashboard.js`.

**Contract:** `dashboard.js` fetches from `/api/changesets/timeseries/`, `/summary/`, and
`/toplist/` (in parallel, several calls — one per chart/KPI group; see the file's `Promise.all`)
and renders the JSON responses into the KPI numbers and charts. Those endpoints are standalone,
public JSON APIs (same query params the dashboard UI uses: `start_date`, `end_date`,
`contributor`, `editor`, `imagery`, plus `group_by`/`dimension`/`metric` depending on the
endpoint) — usable directly by anyone, not just the dashboard's own JS. `DashboardView` itself is
a bare `TemplateView` with no `get_context_data` — it does no DB access.

**Why:** when the whole chart code lived inline in the template with server-rendered
`window.dashboardData`, any edit caused Claude to rewrite the entire file and risk breaking
layout or charts, and the data was only reachable by loading the HTML page. The split means:
- Chart logic changes → edit `static/js/dashboard.js` only
- HTML/layout changes → edit `dashboard.html` only
- Backend/data changes → edit `views.py` only
- The aggregated data itself is a real API endpoint other tools can call directly

### Stats split into timeseries/summary/toplist, not one bundled endpoint
`TimeseriesView`/`SummaryView`/`ToplistView` replaced a single `ChangesetStatsView` that returned
everything (daily counts, every top-N breakdown, object totals) in one bundled response. Split by
resource *shape* (analogous to Datadog's widget types), not by business concept: `timeseries`
handles anything date-bucketed (plain volume via `group_by=none`, or per-name series via
`group_by=editor|imagery|locale|contributor`), `toplist` handles any ranked list
(`dimension` × `metric=count|objects`), `summary` is the handful of single-number KPIs. This
means a `contributor`×`count` or `locale`×`objects` toplist — combinations the old bundled
endpoint never exposed — are just other parameter values on the same endpoint, not new code.
Shared filter-resolution/queryset logic lives in module-level helpers in `views.py`
(`_resolve_range_and_filters`, `_filtered_changesets`, `DIMENSION_FIELDS`) rather than being
duplicated per view. Trade-off: the dashboard now makes ~10 parallel requests to render instead
of 1 — acceptable since they're fetched concurrently (bounded by the slowest, not the sum) and
each is now independently small/cacheable, but a real cost avoided if the "aggregate everything"
endpoint had stayed.

### Public API + docs
All DRF views carry `@extend_schema` annotations (drf-spectacular) so `/api/docs/` stays
accurate as new endpoints/params are added — update the annotation in `views.py` alongside any
signature change, don't just rely on the docstring.

### imagery_used stored as JSON array
`Changeset.imagery_used` is a `JSONField` holding a list of strings (e.g. `["Bing", "Mapbox"]`).
Filtering (`imagery_raw` on `ChangesetQueryView`) uses `__contains`, which on Postgres compiles to
`jsonb`'s native `@>` containment operator — a real DB-level query, not Python-side filtering.

### SequenceState
A single-row model (`SequenceState`) tracks the last ingested sequence number so that
`poll_sequences` can resume after a crash without re-importing history.

### TimescaleDB hypertable
`changesets_changeset` is a TimescaleDB hypertable (monthly chunks on `created_at`) — see
`docs/ARCHITECTURE.md`'s "Why a hypertable" section for the full reasoning and the PK/unique-
constraint trade-off it required (`id` is no longer DB-enforced-unique; real duplicate protection
is the composite `UNIQUE(changeset_id, created_at)` added in migration
`0018_timescale_hypertable`). Django ORM code is otherwise unaffected.

### Design for full history, not just the current subset
Only the past year of changesets is imported today (~23M rows), but the eventual goal is full
2005-present OSM history (~190M+ rows, ~8x bigger). When adding a query, index, or background
job, sanity-check it against "does this still work at ~8x the row count" rather than just
today's data size — see `TODO.md`'s `refresh_rollups()` entry for a concrete example of a design
that already stopped scaling well before that point. Prefer incremental/watermark-based designs
over periodic full-table rebuilds.

## File map

| Path | Role |
|---|---|
| `changesets/models.py` | `Changeset` (hypertable) + rollup/state/job models |
| `changesets/views.py` | All views (dashboard, API, import landing) |
| `changesets/serializers.py` | DRF serializer for `Changeset` |
| `changesets/urls.py` | API URL patterns (`/api/…`) |
| `osm_changeset_api/urls.py` | Root URL conf (mounts API + dashboard) |
| `changesets/osm_fetcher.py` | Fetches & parses OSM replication XML |
| `changesets/rollups.py` | Precomputed daily aggregates behind the unfiltered dashboard view |
| `changesets/management/commands/poll_sequences.py` | Long-running poller |
| `changesets/management/commands/import_from_dump.py` | Bulk planet-dump importer |
| `changesets/templates/changesets/dashboard.html` | Dashboard HTML shell only |
| `static/js/dashboard.js` | All Chart.js chart initialisation |
| `static/output.css` | Compiled Tailwind CSS |
| `db/init/` | One-time Postgres setup (extensions, Datadog schema) for a fresh DB |
| `docs/ARCHITECTURE.md` | Deep dive: data flow, why TimescaleDB, observability, deployment |

## Development notes

- Static files in `DEBUG` mode are served by Django via `django.conf.urls.static`.
- Initial sequence on first poller run: pass `--start <seq>` or set `INITIAL_SEQUENCE` env var.
- `TimeseriesView`/`SummaryView`/`ToplistView` (aggregated stats) default to the last 7 days when
  no date params are given; `ChangesetQueryView` (raw record list) defaults to the last 24 hours
  — it has no unfiltered "everything" mode, see its docstring in `views.py`.
