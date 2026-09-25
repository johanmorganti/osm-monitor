# OSM Monitor

A Django application that continuously ingests [OpenStreetMap](https://www.openstreetmap.org)
changeset data, stores it in a TimescaleDB hypertable, and serves it through a Chart.js analytics
dashboard and a public REST API.

For the deeper "how it actually works" write-up (data flow, why a hypertable, the two ingestion
paths, observability) see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). For architectural
*decisions* and their reasoning (aimed at whoever — human or AI agent — is about to change this
code), see [`CLAUDE.md`](CLAUDE.md). For known issues and deferred work, see
[`TODO.md`](TODO.md). To run it, see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Features

- **Overview dashboard** — changeset volume over time, top contributors/editors/imagery
  providers/countries, objects changed, and a changeset density map, with date-range and
  per-dimension filters. The page shell renders instantly and fetches its data client-side from
  the API below.
- **Editors page** — one column per top editor family, each with its own version breakdown and
  volume-over-time graph.
- **Public REST API** — filterable raw changeset records, plus a small set of aggregate
  endpoints (time series, summary totals, top-N rankings, geo density) usable independently of
  the dashboard. Fully documented and explorable at `/api/docs/` (Swagger UI, auto-generated from
  the code via drf-spectacular — that page is always the source of truth for exact
  params/responses, not this README).
- **Two ingestion paths**: a continuous background poller that tails
  [planet.osm.org](https://planet.osm.org)'s minutely replication feed, and a bulk importer for
  OSM's full changesets dump (all history since 2005, much faster than the minutely feed).
- **TimescaleDB-backed**: the changeset table is a hypertable partitioned by month, so
  date-range queries — the overwhelming majority of real traffic here — only touch the relevant
  chunks, and the dashboard's aggregates are served from continuous aggregates rather than the
  raw table.
- **Structured JSON logs**, with optional Datadog APM tracing and Postgres Database Monitoring.

## Requirements

Postgres with TimescaleDB and PostGIS is required always — there's no SQLite fallback (the schema
relies on Postgres/Timescale-specific SQL throughout: the hypertable, continuous aggregates,
PostGIS centroids, expression indexes). Python 3.12, dependencies in `requirements.txt`.

## Getting data in

**Continuous poller** (how this stays up to date in normal operation):
```bash
python manage.py poll_sequences                    # resumes from DB state automatically
python manage.py poll_sequences --start 6200000     # first run only, if starting fresh
python manage.py poll_sequences --reset --backfill-days 14   # one-time: also backfill N days behind live
```
It saves its position after every sequence, so it resumes safely after a crash or restart.
`--start` can also be set via the `INITIAL_SEQUENCE` environment variable instead. On first run
without `--reset`, it backfills 365 days of minutely sequences behind the live point.

**Bulk dump import** (full history — much faster than the minutely feed):
```bash
python manage.py import_from_dump path/to/changesets-latest.osm[.bz2] --skip N
```
Streams and parses OSM's full [changesets planet dump](https://planet.openstreetmap.org/planet/)
without loading it into memory, using the same batched existence-check as the poller, so it's
safe to run concurrently with live polling. Refreshes every continuous aggregate over the
imported range at the end. See `docs/ARCHITECTURE.md` for when/why to use this over the poller.

## API

Full reference: `/api/docs/` (interactive) or `/api/schema/` (raw OpenAPI). Highlights:

| Endpoint | What it returns |
|---|---|
| `GET /api/changesets/` | Raw changeset records, paginated, always date-bounded (24h default) |
| `GET /api/changesets/timeseries/` | Volume over time, optionally split by dimension |
| `GET /api/changesets/summary/` | Total changesets / objects changed / average, for a range |
| `GET /api/changesets/toplist/` | Top N by count or objects changed, for one dimension |
| `GET /api/changesets/geo/` | Changeset density per geohash-derived grid cell, for the map |
| `GET /api/autocomplete/` | Known contributor/editor/imagery/country values matching a partial query |

All the aggregate endpoints share the same filters (`start_date`, `end_date`, `contributor`,
`editor`, `imagery`, `country`) and default to the last 7 days if no dates are given.

## Project structure

```
changesets/
  models.py                      # Changeset (hypertable), CAgg models, FilterValue, job state
  views.py                       # Dashboard/Editors shells + every API view
  serializers.py                 # DRF serializer for Changeset
  urls.py                        # /api/... URL patterns
  osm_fetcher.py                 # Fetches & parses OSM replication XML, batched upsert logic
  rollups.py                     # FilterValue autocomplete incremental refresh
  cagg_maintenance.py            # Explicit continuous-aggregate refreshes over a date range
  geo.py                         # Geohash/grid SQL fragments shared by GeoView and its CAgg
  migrations/                    # Includes 0018_timescale_hypertable (the Timescale conversion)
  data/                          # country_boundaries.geojson (loaded by load_country_boundaries)
  management/commands/
    poll_sequences.py            # Continuous poller (live + one-time backfill)
    import_from_dump.py          # Bulk planet-dump importer
    load_country_boundaries.py   # Loads country_boundaries from the committed GeoJSON
    refresh_rollups.py           # Manual full-rebuild escape hatch (rarely needed)
    backfill_*.py                # One-off backfills for schema changes over existing data
  templates/changesets/
    dashboard.html               # Overview page HTML shell only — no server-rendered data
    editors.html                 # Editors page HTML shell only
    changesets.html              # Poller status page (/changeset_import/)
static/js/
  common.js                      # Shared chart/map/autocomplete helpers
  dashboard.js, editors.js       # Per-page widget wiring; fetch from the API above
osm_changeset_api/
  urls.py, settings.py, logging_json.py
db/init/                         # One-time Postgres setup for a fresh data directory
docs/
  ARCHITECTURE.md                # Deep dive: data flow, why TimescaleDB, observability
  DEPLOYMENT.md                  # Running it: Docker Compose, optional Datadog, full-history import
  todo/                          # Detail files for TODO.md's index (one per open item)
```
