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
- `/api/changesets/stats/` → `ChangesetStatsView` (aggregated stats behind the dashboard's charts)
- `/api/sequence/<start>/<end>/` → `ChangesetListView` (one-shot import)
- `/api/docs/` → Swagger UI (drf-spectacular) for the public API, `/api/schema/` for the raw OpenAPI schema
- `/changeset_import/` → `APILandingPageView` (import helper UI)
- `manage.py poll_sequences` → continuous background poller

## Architecture decisions

### Template / JS separation, async data fetch
`dashboard.html` is a thin HTML shell only (no server-rendered chart data) — it renders
instantly and reads only `{{ request.GET.xxx }}` for filter-input defaults. All Chart.js
initialisation lives in `static/js/dashboard.js`.

**Contract:** `dashboard.js` calls `fetch('/api/changesets/stats/' + window.location.search)`
client-side and renders the JSON response into the KPI numbers and charts. That endpoint is a
standalone, public JSON API (same query params the dashboard UI uses: `start_date`, `end_date`,
`contributor`, `editor`, `imagery`) — usable directly by anyone, not just the dashboard's own JS.
`DashboardView` itself is a bare `TemplateView` with no `get_context_data` — it does no DB access.

**Why:** when the whole chart code lived inline in the template with server-rendered
`window.dashboardData`, any edit caused Claude to rewrite the entire file and risk breaking
layout or charts, and the data was only reachable by loading the HTML page. The split means:
- Chart logic changes → edit `static/js/dashboard.js` only
- HTML/layout changes → edit `dashboard.html` only
- Backend/data changes → edit `views.py` only
- The aggregated data itself is a real API endpoint other tools can call directly

### Public API + docs
All DRF views carry `@extend_schema` annotations (drf-spectacular) so `/api/docs/` stays
accurate as new endpoints/params are added — update the annotation in `views.py` alongside any
signature change, don't just rely on the docstring.

### imagery_used stored as JSON array
`Changeset.imagery_used` is a `JSONField` holding a list of strings (e.g. `["Bing", "Mapbox"]`).
Filtering uses `__isnull` + Python-side filtering rather than a DB-level array contains, because
the project supports SQLite in development.

### SequenceState
A single-row model (`SequenceState`) tracks the last ingested sequence number so that
`poll_sequences` can resume after a crash without re-importing history.

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
| `changesets/models.py` | `Changeset` + `SequenceState` models |
| `changesets/views.py` | All views (dashboard, API, import landing) |
| `changesets/serializers.py` | DRF serializer for `Changeset` |
| `changesets/urls.py` | API URL patterns (`/api/…`) |
| `osm_changeset_api/urls.py` | Root URL conf (mounts API + dashboard) |
| `changesets/osm_fetcher.py` | Fetches & parses OSM replication XML |
| `changesets/management/commands/poll_sequences.py` | Long-running poller |
| `changesets/templates/changesets/dashboard.html` | Dashboard HTML shell only |
| `static/js/dashboard.js` | All Chart.js chart initialisation |
| `static/output.css` | Compiled Tailwind CSS |

## Development notes

- Static files in `DEBUG` mode are served by Django via `django.conf.urls.static`.
- Initial sequence on first poller run: pass `--start <seq>` or set `INITIAL_SEQUENCE` env var.
- `ChangesetStatsView` (aggregated stats) defaults to the last 7 days when no date params are
  given; `ChangesetQueryView` (raw record list) defaults to the last 24 hours — it has no
  unfiltered "everything" mode, see its docstring in `views.py`.
