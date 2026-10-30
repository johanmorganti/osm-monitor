# CLAUDE.md — OSM Monitor

Instructions for AI agents working on this repo. The reasoning behind each rule lives in
[`docs/decisions/`](docs/decisions/README.md) (one file per decision); read the linked file before
changing anything in its area. How the system works: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
(pages, endpoints, data flow). Running it: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Open work:
[`TODO.md`](TODO.md) — check it before starting non-trivial work, and keep it current.

Django app that ingests OSM changeset replication and object diffs from planet.osm.org into
ClickHouse, and serves a Chart.js dashboard plus a public JSON API from it. The app's own state
(the pollers' positions, Django's tables) is a SQLite file. Postgres/TimescaleDB were removed on
2026-10-05 ([timescale-removal](docs/decisions/timescale-removal.md)).

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
- **MCP tools are the REST endpoints**: a tool calls its endpoint's view and takes its description
  from the OpenAPI schema, so it never gets its own query or parameter handling. A new endpoint
  becomes a tool with one line in `TOOL_ENDPOINTS`. → [mcp-server](docs/decisions/mcp-server.md)
- **Dimension names: DB column, API param and UI label may differ, but each layer is consistent
  everywhere.** Before adding or changing a label, grep the templates and JS for every existing
  string for that dimension. A public API param is never renamed or removed casually (breaking
  change). → [dimension-naming](docs/decisions/dimension-naming.md) (has the mapping table)
- **No scripts or stylesheets from CDNs**: libraries go in `static/vendor/` (see its README), and
  Tailwind is compiled at image build from the templates and `static/js`, so a class must appear
  whole there (no `` `bg-${color}-500` ``). → [self-hosted-assets](docs/decisions/self-hosted-assets.md)
- **Untagged volume goes in the `(none)` bucket (`NONE_BUCKET`), never silently excluded**; it
  filters to the NULL rows. → [none-bucket](docs/decisions/none-bucket.md)

### Analytics backends
- **Public-API behavior goes in `changesets/api/`, never in a backend**; how a backend gets the
  numbers stays inside it. Contract: `changesets/analytics/base.py`.
  → [analytics-backends](docs/decisions/analytics-backends.md)
- **ClickHouse is the only analytics store** (backends `clickhouse`, and `clickhouse_raw` without
  its rollups, for comparisons). SQLite holds only app state (two poller rows, Django's tables):
  nothing analytical goes there. → [timescale-removal](docs/decisions/timescale-removal.md)
- **ClickHouse rollups are refreshable materialized views, never incremental ones**
  (`changesets` receives new versions of existing changesets). Queries read a rollup up to its
  watermark and the raw table after it.
- **A behavior-neutral change is proven neutral**: snapshot the fixed API calls before and after
  (byte-identical) and diff the OpenAPI schema; `check_backend_parity` compares ClickHouse with
  and without its rollups.
- **Design for full history** (~190M+ rows, 2005 to today): check new queries/jobs against the
  full row count; prefer incremental, watermarked designs, watermarked on `created_at`.
  → [full-history](docs/decisions/full-history.md)
- **The contributor filter and its skip index share one expression, `lower(user)`**: change
  them together, or the index silently stops being used.
  → [contributor-filter](docs/decisions/contributor-filter.md)
- **Old-dated rows in the replication stream are normal** (comments resurface old changesets).
  Don't "fix" them.
  → [old-dated-rows](docs/decisions/old-dated-rows.md)
- **Object tables (`poll_diffs`): every row is keyed by its source file**, so replays replace;
  never read `object_versions` over more than a few days from the API (daily rollups instead).
  Check counts against `changes_count`. → [object-changes](docs/decisions/object-changes.md)
- **Location rules (geohash, country) live in `changesets/ingest/locate.py` only**; after a
  change run `recompute_locations --check`. → [location-at-ingest](docs/decisions/location-at-ingest.md)
- **Geo: one geohash key, coarser cells are prefixes.** Check what actually reaches a query (is
  the input spatially bounded?), and any resolution reachable from several request shapes must
  adapt to the request. → [geo-geohash](docs/decisions/geo-geohash.md)

### Operations
- **Tests need no ClickHouse and no network** (`changesets/tests/`; a guard fails any test that reaches
  it, as the container's environment points at production). `deploy.sh` runs them in the freshly built
  image and stops on a failure. A fix to parsing, validation or an ingest rule gets a test with it.
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
| `changesets/views.py` | HTML page shells only (Overview, Objects, Editors, API / MCP, poller status) |
| `changesets/api/` | Public JSON API, backend-agnostic: `views.py` (endpoints + OpenAPI annotations), `params.py` (parsing, defaults, `pick_interval`), `sizes.py` (size histogram buckets, percentile definition), `schema.py` |
| `changesets/mcp/` | MCP server at `/mcp`: `server.py` (JSON-RPC over Streamable HTTP, instructions), `tools.py` (one tool per API endpoint, from the OpenAPI schema) |
| `changesets/analytics/` | Analytics backend interface (`base.py`: `Filters`, `AnalyticsBackend`, `NONE_BUCKET`, `DIMENSIONS`) + `registry.py` (`ANALYTICS_BACKEND`) |
| `changesets/analytics/clickhouse/` | ClickHouse backend (`backend.py`), client, schema (`schema/*.sql`, applied by `clickhouse_migrate`) |
| `changesets/tests/` | Unit tests: API param parsing, MCP protocol, ingest rules (editor family, feature, osmChange), env reader; no ClickHouse |
| `changesets/models.py` | `SequenceState`, `DiffState`: the pollers' positions, in SQLite (`SQLITE_PATH`, WAL mode set in `apps.py`) |
| `changesets/serializers.py` | DRF serializer for raw changeset records (`/api/changesets/`) |
| `changesets/urls.py` | API URL patterns (`/api/…`) |
| `osm_changeset_api/urls.py` | Root URL conf (mounts API + pages) |
| `changesets/osm_fetcher.py` | Fetches & parses OSM replication XML |
| `changesets/ingest/osmchange.py` | Streaming osmChange (replication diff) parser + feature classification |
| `changesets/ingest/objects.py` | Writes parsed diffs to the ClickHouse object tables (`DiffWriter`) |
| `changesets/management/commands/poll_diffs.py` | Diff poller (minutely live + daily backfill), `diff-poller` service |
| `changesets/ingest/reconcile.py` | Re-fetches from the OSM API changesets still open 25 h after creation (hourly from the poller; `reconcile_open_changesets`) |
| `changesets/ingest/locate.py` | geohash + country for parsed changesets (Shapely/pyproj), used at ingest |
| `changesets/ingest/writers/` | Ingestion storage writers (`base.py` contract, `clickhouse.py`), selected by `INGEST_BACKENDS` |
| `changesets/geo.py` | Geohash helpers (precision per viewport, bbox cover, encode/decode) and the bbox-size threshold for the map |
| `changesets/management/commands/poll_sequences.py` | Long-running poller |
| `changesets/management/commands/import_from_dump.py` | Bulk planet-dump importer |
| `changesets/templates/changesets/{dashboard,objects,editors,api_docs}.html` | Page HTML shells only (`_drilldown_panel.html`: the drill-down panel both Overview and Objects include) |
| `static/js/common.js` | Shared chart factories (clickable points: `pointClickOptions`), `apiUrl`/`fetchJson`/`loadWidget` (lazy), geo map, drill-down panel (`openDrilldown`), autocomplete |
| `static/js/{dashboard,objects,editors,api_docs}.js` | Page-specific widget wiring |
| `tailwind/` | Tailwind config and input; compiled to `static/css/site.css` (every page) in the Dockerfile, or by `make css` |
| `static/vendor/` | Front-end libraries (Chart.js, Leaflet, MapLibre, Swagger UI), one directory per package version; `README.md` has provenance and hashes |
| `docs/decisions/` | Architecture decisions, one per file (index: `README.md`) |
| `docs/ARCHITECTURE.md` | Pages and endpoints, data flow, observability |
| `docs/DEPLOYMENT.md` | Running the Compose stack, optional Datadog overlay, full-history import |
