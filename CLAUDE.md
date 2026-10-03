# CLAUDE.md — OSM Monitor

Instructions for AI agents working on this repo. The reasoning behind each rule lives in
[`docs/decisions/`](docs/decisions/README.md) (one file per decision); read the linked file before
changing anything in its area. How the system works: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
(pages, endpoints, data flow). Running it: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Open work:
[`TODO.md`](TODO.md) — check it before starting non-trivial work, and keep it current.

Django app that ingests OSM changeset replication from planet.osm.org into ClickHouse (analytics,
the default backend) and a deprecated TimescaleDB copy in Postgres (which also holds the app's own
state), and serves a Chart.js dashboard plus a public JSON API.

## Rules

### API and pages
- **Pages are HTML shells; all data comes from the public JSON API.** Chart logic in
  `static/js/<page>.js`, layout in the template, API behavior in `changesets/api/`. Views do no DB
  access. → [template-js-separation](docs/decisions/template-js-separation.md)
- **New charts are parameters on existing endpoint shapes** (timeseries / summary / toplist / …)
  before they are new endpoints. Widgets go through `loadWidget` (lazy, fetched near the
  viewport); `{ eager: true }` only when the response fills something above the fold.
  → [api-endpoint-shapes](docs/decisions/api-endpoint-shapes.md)
- **Every endpoint/param change updates its `@extend_schema` annotation** in
  `changesets/api/views.py`, so `/api/docs/` stays accurate. Don't rely on the docstring.
- **Dimension names: DB column, API param and UI label may differ, but each layer is consistent
  everywhere.** Before adding or changing a label, grep the templates and JS for every existing
  string for that dimension. A public API param is never renamed or removed casually (breaking
  change). → [dimension-naming](docs/decisions/dimension-naming.md) (has the mapping table)
- **Untagged volume goes in the `(none)` bucket (`NONE_BUCKET`), never silently excluded**; it
  filters to the NULL rows. → [none-bucket](docs/decisions/none-bucket.md)

### Analytics backends
- **Public-API behavior goes in `changesets/api/`, never in a backend**; how a backend gets the
  numbers stays inside it. Contract: `changesets/analytics/base.py`.
  → [analytics-backends](docs/decisions/analytics-backends.md)
- **ClickHouse first; TimescaleDB is deprecated.** No new CAggs or Timescale-only features; a new
  backend method may raise `NotImplementedError` on Timescale (the API answers 501). Postgres
  stays (Django tables, `SequenceState`).
- **ClickHouse rollups are refreshable materialized views, never incremental ones**
  (`changesets` receives new versions of existing changesets). Queries read a rollup up to its
  watermark and the raw table after it.
- **A behavior-neutral change is proven neutral**: snapshot the fixed API calls before and after
  (byte-identical) and diff the OpenAPI schema; `check_backend_parity` compares backends.
- **Design for full history** (~190M+ rows, 2005 to today): check new queries/jobs against the
  full row count; prefer incremental, watermarked designs, watermarked on `created_at`.
  → [full-history](docs/decisions/full-history.md)
- **Old-dated rows in the replication stream are normal** (comments resurface old changesets).
  Don't "fix" them, and don't widen the 7-day CAgg refresh window.
  → [old-dated-rows](docs/decisions/old-dated-rows.md)
- **Location rules (geohash, country) live in `changesets/ingest/locate.py` only**; after a
  change run `recompute_locations --check`. → [location-at-ingest](docs/decisions/location-at-ingest.md)
- **Geo: one geohash key, coarser cells are prefixes.** Check what actually reaches a query (is
  the input spatially bounded?), and any resolution reachable from several request shapes must
  adapt to the request. → [geo-geohash](docs/decisions/geo-geohash.md)
- **Timescale raw-table filters use equality/`IN`, never `__iexact`/`__icontains`** (bloom
  filters on compressed chunks); a new filterable column needs a plain btree index.
  → [timescale-storage](docs/decisions/timescale-storage.md)

### Operations
- **Every new long-running management command or backfill runs `SET statement_timeout = 0` on its
  own connection** (the role default is 120s, `web` 30s); re-issue it after any reconnect.
  → [statement-timeout](docs/decisions/statement-timeout.md)
- **Several sessions share this checkout.** Stage only your own files (never `git add -A`), and
  remember `deploy.sh` builds whatever is in the working tree, others' work in progress included.

## Documentation rules

- **CLAUDE.md stays agent instructions**: a rule in one or two lines with a link. Reasoning,
  evidence, measurements and lessons go in `docs/decisions/<slug>.md` (new file + a line in
  `docs/decisions/README.md`, or a dated note in an existing file). When a rule changes, update
  its decision file and its line here in the same change.
  → [documentation-layout](docs/decisions/documentation-layout.md)
- **`TODO.md` is an index**: one line per item, linking to `docs/todo/<slug>.md` for the details.
  A new deferred item gets both, never a paragraph in `TODO.md`. When an item is resolved, delete
  its line and file, and move any durable "why" to `docs/decisions/`. Never leave a
  `docs/todo/*.md` without a `TODO.md` line pointing to it; when a topic is split, merged or
  superseded, update the link text and the file's contents together.
- No server- or data-specific state (hosts, current imports) in CLAUDE.md, TODO.md or the README:
  deployment specifics go in `docs/DEPLOYMENT.md`.

## File map

| Path | Role |
|---|---|
| `changesets/views.py` | HTML page shells only (Overview, Objects, Editors, poller status) |
| `changesets/api/` | Public JSON API, backend-agnostic: `views.py` (endpoints + OpenAPI annotations), `params.py` (parsing, defaults, `pick_interval`), `sizes.py` (size histogram buckets, percentile definition), `schema.py` |
| `changesets/analytics/` | Analytics backend interface (`base.py`: `Filters`, `AnalyticsBackend`, `NONE_BUCKET`, `DIMENSIONS`) + `registry.py` (`ANALYTICS_BACKEND`) |
| `changesets/analytics/clickhouse/` | ClickHouse backend (`backend.py`), client, schema (`schema/*.sql`, applied by `clickhouse_migrate`) |
| `changesets/analytics/timescale/` | TimescaleDB backend (deprecated): CAgg routing (`caggs.py`), raw fallback + queries (`backend.py`), exact-value filter resolution (`canonical.py`) |
| `changesets/models.py` | `SequenceState` + the deprecated Timescale `Changeset` (hypertable) and rollup models |
| `changesets/serializers.py` | DRF serializer for `Changeset` |
| `changesets/urls.py` | API URL patterns (`/api/…`) |
| `osm_changeset_api/urls.py` | Root URL conf (mounts API + pages) |
| `changesets/osm_fetcher.py` | Fetches & parses OSM replication XML |
| `changesets/ingest/locate.py` | geohash + country for parsed changesets (Shapely/pyproj), used at ingest |
| `changesets/ingest/writers/` | Ingestion storage writers (`base.py` contract, `clickhouse.py`, `timescale.py`), selected by `INGEST_BACKENDS` |
| `changesets/rollups.py` | Legacy Postgres rollups + `FilterValue` refresh (Timescale side) |
| `changesets/geo.py` | Geohash helpers (precision per viewport, bbox cover, encode/decode) + grid/bbox SQL for the Timescale geo queries |
| `changesets/management/commands/poll_sequences.py` | Long-running poller |
| `changesets/management/commands/import_from_dump.py` | Bulk planet-dump importer |
| `changesets/templates/changesets/{dashboard,objects,editors}.html` | Page HTML shells only |
| `static/js/common.js` | Shared chart factories, `apiUrl`/`fetchJson`/`loadWidget` (lazy), geo map, autocomplete |
| `static/js/{dashboard,objects,editors}.js` | Page-specific widget wiring |
| `static/output.css` | Compiled Tailwind CSS (poller status page only; the other pages use the Tailwind CDN) |
| `db/init/` | One-time Postgres setup (extensions, Datadog schema) for a fresh DB |
| `docs/decisions/` | Architecture decisions, one per file (index: `README.md`) |
| `docs/ARCHITECTURE.md` | Pages and endpoints, data flow, observability |
| `docs/DEPLOYMENT.md` | Running the Compose stack, optional Datadog overlay, full-history import |
