# CLAUDE.md — OSM Monitor

Project context and architectural decisions for AI-assisted development.

## Project overview

Django app that ingests OSM changeset replication sequences from planet.osm.org, stores them in a
database, and serves a Chart.js dashboard plus a REST API.

Key entry points:
- `/` → `DashboardView` (Chart.js dashboard)
- `/api/changesets/` → `ChangesetQueryView` (filterable REST API)
- `/api/sequence/<start>/<end>/` → `ChangesetListView` (one-shot import)
- `/changeset_import/` → `APILandingPageView` (import helper UI)
- `manage.py poll_sequences` → continuous background poller

## Architecture decisions

### Template / JS separation (2026-03-20)
`dashboard.html` is a thin HTML shell only. All Chart.js initialisation lives in
`static/js/dashboard.js`.

**Contract:** the template sets `window.dashboardData = { ... }` using Django context variables
serialised with `json.dumps(..., cls=DjangoJSONEncoder)` and emitted with `{{ ...|safe }}`.
`dashboard.js` reads from that global — it never contains Django template syntax.

**Why:** when the whole chart code lived inline in the template, any edit caused Claude to
rewrite the entire 335-line file and risk breaking layout or charts. The split means:
- Chart logic changes → edit `static/js/dashboard.js` only
- HTML/layout changes → edit `dashboard.html` only
- Backend/data changes → edit `views.py` only

### Data serialisation
`DashboardView.get_context_data` builds Python lists/dicts, serialises them with
`json.dumps(..., cls=DjangoJSONEncoder)`, and puts the resulting JSON strings in context.
The template emits them raw with `|safe` (no extra `JSON.parse` needed in JS — the values are
already valid JS literals).

### imagery_used stored as JSON array
`Changeset.imagery_used` is a `JSONField` holding a list of strings (e.g. `["Bing", "Mapbox"]`).
Filtering uses `__isnull` + Python-side filtering rather than a DB-level array contains, because
the project supports SQLite in development.

### SequenceState
A single-row model (`SequenceState`) tracks the last ingested sequence number so that
`poll_sequences` can resume after a crash without re-importing history.

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
- The dashboard defaults to the last 7 days when no date params are provided.
