# OSM Monitor

A Django application that continuously ingests [OpenStreetMap](https://www.openstreetmap.org)
changeset data, stores it in ClickHouse, and serves it through a Chart.js analytics dashboard and a
public REST API.

For the deeper "how it actually works" write-up (data flow, the ingestion
paths, observability) see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). For architectural
*decisions* and their reasoning, see [`docs/decisions/`](docs/decisions/README.md); AI agents
working on the code start from [`CLAUDE.md`](CLAUDE.md). For known issues and deferred work, see
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
- **Object changes**: a second poller reads the replication diffs, which list every object
  version uploaded, and stores counts per changeset by node/way/relation, create/modify/delete
  and feature (kept for all time), plus every object version for the last 92 days (see "How data
  is stored" below).
- **Two ingestion paths**: a continuous background poller that tails
  [planet.osm.org](https://planet.osm.org)'s minutely replication feed, and a bulk importer for
  OSM's full changesets dump (all history since 2005, much faster than the minutely feed).
- **ClickHouse-backed**: every changeset since 2005 in one columnar table, queried directly, plus a
  daily rollup refreshed once a day for the common date-range questions. The API sits on a
  backend interface (`changesets/analytics/`). The app's own state (the pollers' positions) is a
  small SQLite file; the original TimescaleDB/Postgres storage was removed in October 2026.
- **Structured JSON logs**, with optional Datadog APM tracing and ClickHouse Database Monitoring.

## How data is stored

One real example, followed through each table: in a single minute (2026-10-03, 01:14-01:15 UTC),
a StreetComplete user in Osaka answered four quests about the same bridge, one changeset per
quest. (Username replaced; everything else is real data.)

**Today: one row per changeset** (`changesets` in ClickHouse, from the changeset replication
feed and the full-history dump). It says who, with what, where and how many objects, but not
which objects:

| changeset_id | created_at | user | created_by | changes_count | comment | streetcomplete_quest_type | country | geohash |
|---|---|---|---|---|---|---|---|---|
| 189905890 | 2026-10-03 01:14:32 | mapper_a | StreetComplete 63.4 | 1 | Specify maximum allowed weights | AddMaxWeight | JP | xn0mk3 |
| 189905879 | 2026-10-03 01:13:57 | mapper_a | StreetComplete 63.4 | 1 | Specify road surfaces | AddRoadSurface | JP | xn0mk3 |
| 189905881 | 2026-10-03 01:14:04 | mapper_a | StreetComplete 63.4 | 2 | Specify whether there are cycleways | AddCycleway | JP | xn0mk3 |
| 189905882 | 2026-10-03 01:14:06 | mapper_a | StreetComplete 63.4 | 2 | Specify whether roads have lane markings | AddLaneMarkings | JP | xn0mk3 |

**Since 2026-10-03, with the 92 days before backfilled: the objects themselves**, from the
replication osmChange diffs (`poll_diffs`; see
[`docs/decisions/object-changes.md`](docs/decisions/object-changes.md)). Not shown on the
dashboard yet. The diff for that minute contains:

```xml
<modify>
  <way id="148066277" version="12" changeset="189905879" timestamp="2026-10-03T01:14:34Z">
    <nd ref="…"/><nd ref="…"/>
    <tag k="highway" v="unclassified"/><tag k="bridge" v="yes"/><tag k="surface" v="asphalt"/> …
  </way>
</modify>
```

It becomes two kinds of rows:

- **Counts per changeset, kept for all time** (`object_changes`, plus `object_change_features` split
  by the object's main tag key). This is what other OSM statistics tools provide: nodes, ways and
  relations created, modified and deleted, per editor, country or campaign. A changeset can span
  several minutely files, so there is one partial row per file, summed when queried. The
  creates, modifies and deletes of a changeset add up to its `changes_count` above, which serves as
  a built-in check. The rows for this minute's file (189905881 and 189905882 each changed one
  more object, which arrived in another file):

  | changeset_id | type | action | feature | count |
  |---|---|---|---|---|
  | 189905890 | way | modify | highway | 1 |
  | 189905879 | way | modify | highway | 1 |
  | 189905881 | way | modify | highway | 1 |
  | 189905882 | way | modify | highway | 1 |

- **One row per object version, kept for 92 days** (`object_versions`), with everything the diff
  has: tags and geometry. Here the four "1 way modified" rows above turn out to be one object, the
  same bridge edited four times in 15 seconds, each version adding one tag:

  | type | id | version | changeset_id | timestamp | tags added since the previous version |
  |---|---|---|---|---|---|
  | way | 148066277 | 11 | 189905890 | 01:14:32 | (previous version not in the window) |
  | way | 148066277 | 12 | 189905879 | 01:14:34 | `surface=asphalt` |
  | way | 148066277 | 13 | 189905881 | 01:14:44 | `cycleway:both=no` |
  | way | 148066277 | 14 | 189905882 | 01:14:47 | `lane_markings=no` |

  That's what makes re-edits, edit wars and tag-level changes visible. The diff only carries the
  new version of an object, so a tag change can be computed only when the previous version is
  also in the window.

- **Edits to existing objects, kept for all time** (`object_edits`): just time, object, version,
  changeset and contributor for every version after the first, ~3 bytes each. It feeds the
  Objects page's "Most Edited Objects last week" (`/api/objects/most-edited/`).

## Requirements

ClickHouse (every changeset and object, answers the API) and a writable directory for the SQLite
file holding the app's own state. Python 3.12, dependencies in `requirements.txt`.

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

**Object changes** (the `diff-poller` service):
```bash
python manage.py poll_diffs                        # live minutely diffs + 92-day daily backfill
python manage.py poll_diffs --backfill-days 0      # live only
```

**Bulk dump import** (full history — much faster than the minutely feed):
```bash
python manage.py import_from_dump path/to/changesets-latest.osm[.bz2] --skip N
```
Streams and parses OSM's full [changesets planet dump](https://planet.openstreetmap.org/planet/)
without loading it into memory, through the same writer as the poller (re-inserted changesets
collapse into one), so it's safe to run concurrently with live polling. See `docs/ARCHITECTURE.md` for when/why to use this over the poller.

## API

Full reference: `/api/docs/` (interactive) or `/api/schema/` (raw OpenAPI). Highlights:

| Endpoint | What it returns |
|---|---|
| `GET /api/changesets/` | Raw changeset records, paginated, always date-bounded (24h default) |
| `GET /api/changesets/timeseries/` | Volume over time, optionally split by dimension |
| `GET /api/changesets/summary/` | Total changesets / objects changed / average, for a range |
| `GET /api/changesets/toplist/` | Top N by count or objects changed, for one dimension |
| `GET /api/changesets/geo/` | Changeset density per geohash-derived grid cell, for the map |
| `GET /api/objects/most-edited/` | Objects with the most edits over the last 7 days (from the replication diffs) |
| `GET /api/autocomplete/` | Known contributor/editor/imagery/country values matching a partial query |

All the aggregate endpoints share the same filters (`start_date`, `end_date`, `contributor`,
`editor`, `imagery`, `country`) and default to the last 7 days if no dates are given.

## Project structure

```
changesets/
  models.py                      # Poller state (SequenceState, DiffState), in SQLite
  views.py                       # HTML page shells (Overview, Objects, Editors, poller status)
  api/                           # Public JSON API: endpoints, parameter parsing, OpenAPI annotations
  analytics/                     # Analytics backend interface + the ClickHouse implementation
  analytics/clickhouse/schema/   # ClickHouse tables and refreshable rollups (applied by clickhouse_migrate)
  ingest/locate.py               # geohash + country for each changeset, computed at ingest
  ingest/writers/                # Storage writers ingestion feeds (clickhouse)
  ingest/osmchange.py            # Streaming parser for the replication diffs (osmChange)
  ingest/objects.py              # Writes parsed diffs to the ClickHouse object tables
  ingest/reconcile.py            # Re-fetches changesets the feed left open, from the OSM API
  serializers.py                 # DRF serializer for Changeset
  urls.py                        # /api/... URL patterns
  osm_fetcher.py                 # Fetches & parses OSM replication XML, batched upsert logic
  geo.py                         # Geohash helpers for the map and the ingest locator
  migrations/                    # Django migrations for the SQLite state (0001_initial)
  data/                          # country_boundaries.geojson (read at ingest by ingest/locate.py)
  management/commands/
    poll_sequences.py            # Continuous poller (live + one-time backfill)
    poll_diffs.py                # Object changes from the replication diffs (live + daily backfill)
    reconcile_open_changesets.py # Re-fetch changesets still open 25 h after creation (the poller does it hourly)
    clickhouse_migrate.py        # Applies the ClickHouse schema (run by the migrate service)
    import_from_dump.py          # Bulk planet-dump importer
    recompute_locations.py       # Recompute geohash/country in ClickHouse after a location-rule change
    check_backend_parity.py      # Compare ClickHouse with and without its rollups
  templates/changesets/
    dashboard.html               # Overview page HTML shell only — no server-rendered data
    objects.html                 # Objects page HTML shell only
    editors.html                 # Editors page HTML shell only
    changesets.html              # Poller status page (/changeset_import/)
static/js/
  common.js                      # Shared chart/map/autocomplete helpers
  dashboard.js, objects.js, editors.js  # Per-page widget wiring; fetch from the API above
osm_changeset_api/
  urls.py, settings.py, logging_json.py
docs/
  ARCHITECTURE.md                # How it works: pages and endpoints, data flow, observability
  decisions/                     # Architecture decisions and their reasoning, one per file
  DEPLOYMENT.md                  # Running it: Docker Compose, optional Datadog, full-history import
  todo/                          # Detail files for TODO.md's index (one per open item)
```
