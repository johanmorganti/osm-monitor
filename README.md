# OSM Monitor

A Django application that continuously ingests [OpenStreetMap](https://www.openstreetmap.org)
changeset data, stores it in a TimescaleDB hypertable, and serves it through a Chart.js analytics
dashboard and a public REST API.

For the deeper "how it actually works" write-up (data flow, why a hypertable, the two ingestion
paths, observability setup) see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). For architectural
*decisions* and their reasoning (aimed at whoever — human or AI agent — is about to change this
code), see [`CLAUDE.md`](CLAUDE.md). For known issues and deferred work, see
[`TODO.md`](TODO.md).

## Features

- **Dashboard** — Chart.js charts (changeset volume over time, top contributors/editors/
  imageries/locales, objects changed) with date-range and contributor/editor/imagery filters.
  The page shell renders instantly and fetches its data client-side from the API below.
- **Public REST API** — filterable raw changeset records, plus a small set of aggregate
  endpoints (time series, summary totals, top-N rankings) usable independently of the dashboard.
  Fully documented and explorable at `/api/docs/` (Swagger UI, auto-generated from the code via
  drf-spectacular — that page is always the source of truth for exact params/responses, not this
  README).
- **Two ingestion paths**: a continuous background poller that tails
  [planet.osm.org](https://planet.osm.org)'s minutely replication feed, and a bulk importer for
  OSM's full changesets dump (for backfilling history faster than the minutely feed allows).
- **TimescaleDB-backed**: the changeset table is a hypertable partitioned by month, so
  date-range queries — the overwhelming majority of real traffic here — only touch the relevant
  chunks instead of scanning the whole table.
- **Datadog observability**: APM tracing, structured JSON logs correlated to traces, and
  Postgres Database Monitoring (query-level metrics, samples, explain plans), all optional.

## Quick start

Postgres/TimescaleDB is required always — there's no SQLite fallback (the schema relies on
Postgres/Timescale-specific SQL throughout: `rollups.py`'s raw upserts, the expression indexes in
`Changeset.Meta`, the hypertable conversion itself).

### Docker Compose (recommended — matches how this actually runs)

```bash
cp env.example .env   # fill in POSTGRES_PASSWORD at minimum
./deploy.sh           # builds the images and starts db, web, poller
```

Open `http://localhost:5006/` for the dashboard, `http://localhost:5006/api/docs/` for the API.

`deploy.sh` stamps the build with the current git commit (shown as the `version` tag on every
Datadog trace/log) and runs `docker compose build && docker compose up -d`. First boot creates
the schema and converts `changesets_changeset` into a TimescaleDB hypertable automatically (see
migration `0018_timescale_hypertable`); Datadog's `pg_stat_statements`/`datadog` schema setup
also runs automatically on a fresh volume (`db/init/`) — see that directory's own comments for
the one manual step needed if you're re-running it against an *existing* (non-fresh) volume.

### Running `manage.py` locally against the dockerized DB

A lighter loop than a full image rebuild for quick script/shell work — start only `db`, point a
local venv at its exposed port:

```bash
docker compose up -d db
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp env.example .env
# In .env, change DATABASE_URL's host:port from db:5432 to localhost:5007
python manage.py migrate
python manage.py runserver
```

## Getting data in

Three ways, from smallest to largest:

**One-shot sequence range** (for testing, or backfilling a small window):
```
GET /api/sequence/<seq_start>/<seq_end>/
```
`seq_start`/`seq_end` are OSM replication sequence numbers (find the latest at
<https://planet.osm.org/replication/changesets/state.yaml>); max 10,000 sequences per call.
Returns a job ID — poll `GET /api/import-job/<job_id>/` for progress.

**Continuous poller** (how this stays up to date in normal operation):
```bash
python manage.py poll_sequences                    # resumes from DB state automatically
python manage.py poll_sequences --start 6200000     # first run only, if starting fresh
python manage.py poll_sequences --reset --backfill-days 14   # one-time: also backfill N days behind live
```
This is what `docker-compose.yml`'s `poller` service runs continuously. It saves its position
after every sequence, so it resumes safely after a crash or restart. `--start` can also be set via
the `INITIAL_SEQUENCE` environment variable instead.

**Bulk dump import** (for a large historical backfill — much faster than the minutely feed):
```bash
python manage.py import_from_dump path/to/changesets-latest.osm[.bz2] --skip N
```
Streams and parses OSM's full changesets planet dump without loading it into memory, using the
same batched existence-check as the poller so it's safe to run concurrently with live polling.
See `docs/ARCHITECTURE.md` for when/why to use this over the poller.

## API

Full reference: `/api/docs/` (interactive) or `/api/schema/` (raw OpenAPI). Highlights:

| Endpoint | What it returns |
|---|---|
| `GET /api/changesets/` | Raw changeset records, paginated, always date-bounded (24h default) |
| `GET /api/changesets/<id>/` | Single changeset |
| `GET /api/changesets/timeseries/` | Volume over time, optionally split by dimension |
| `GET /api/changesets/summary/` | Total changesets / objects changed / average, for a range |
| `GET /api/changesets/toplist/` | Top N by count or objects changed, for one dimension |
| `GET /api/autocomplete/` | Known contributor/editor/imagery values matching a partial query |
| `GET /api/sequence/<start>/<end>/`, `GET /api/import-job/<id>/` | One-shot import + progress |

All the aggregate endpoints share the same filters (`start_date`, `end_date`, `contributor`,
`editor`, `imagery`) and default to the last 7 days if no dates are given.

## Project structure

```
changesets/
  models.py                      # Changeset (hypertable), rollup tables, FilterValue, job state
  views.py                       # Dashboard shell + every API view
  serializers.py                 # DRF serializer for Changeset
  urls.py                        # /api/... URL patterns
  osm_fetcher.py                 # Fetches & parses OSM replication XML, batched upsert logic
  rollups.py                     # Precomputed daily aggregates behind the unfiltered dashboard view
  migrations/                    # Includes 0018_timescale_hypertable (the Timescale conversion)
  management/commands/
    poll_sequences.py            # Continuous poller (live + one-time backfill)
    import_from_dump.py          # Bulk planet-dump importer
    backfill_filter_values.py    # One-time FilterValue seed for existing data
    refresh_rollups.py           # Manual full-rebuild escape hatch (rarely needed)
  templates/changesets/
    dashboard.html                # HTML shell only — no server-rendered data
    changesets.html                # Import helper UI
static/js/dashboard.js            # All Chart.js logic; fetches from the API above
osm_changeset_api/
  urls.py, settings.py, logging_json.py
db/init/                          # One-time Postgres setup (extensions, Datadog schema) for a fresh DB
docs/
  ARCHITECTURE.md                 # Deep dive: data flow, why TimescaleDB, observability, deployment
  db-benchmark-plan.md            # Partitioned-Postgres-vs-TimescaleDB benchmark brief (historical)
docker-compose.yml, Dockerfile, entrypoint.sh, deploy.sh
```
