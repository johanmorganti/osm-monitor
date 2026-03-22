# OSM Monitor

A Django application that continuously ingests OpenStreetMap changeset replication sequences,
stores them in a database, and provides a Chart.js analytics dashboard and a REST API.

## Features

- **Dashboard** — interactive Chart.js charts (changesets over time, top contributors, top editors,
  top imageries) with a date-range filter
- **REST API** — filterable changeset query endpoint with pagination
- **Background poller** — management command that tails planet.osm.org replication sequences in
  real time

## Quick start

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open `http://127.0.0.1:8000/` for the dashboard.

## Importing changesets

### One-shot import (small range)

```
GET /api/sequence/<seq_start>/<seq_end>/
```

`seq_start` and `seq_end` are OSM replication sequence numbers. Find the latest at
<https://planet.osm.org/replication/changesets/state.yaml>. Maximum range: 10 000 sequences per
request.

### Continuous poller

```bash
# First run — provide a starting sequence
python manage.py poll_sequences --start 6200000

# Subsequent runs resume automatically from DB state
python manage.py poll_sequences

# Custom poll interval (default 60 s)
python manage.py poll_sequences --interval 30
```

The poller saves its position after every sequence so it can resume safely after a crash.
You can also set the starting sequence via the `INITIAL_SEQUENCE` environment variable.

## REST API

| Endpoint | Description |
|---|---|
| `GET /api/changesets/` | Query changesets (see filters below) |
| `GET /api/changesets/<id>/` | Single changeset detail |
| `GET /api/sequence/<start>/<end>/` | Import a sequence range |

### Query parameters for `/api/changesets/`

| Param | Example | Description |
|---|---|---|
| `start_date` | `2024-01-01` | Filter by `created_at >=` |
| `end_date` | `2024-01-31` | Filter by `created_at <=` |
| `user` | `johndoe` | OSM username |
| `editor` | `iD` | Editor family (e.g. iD, JOSM, StreetComplete) |
| `hashtag` | `#hotosm` | Changeset hashtag |
| `imagery` | `Bing` | Imagery used |
| `bbox` | `2.2,48.8,2.4,48.9` | Bounding box `min_lon,min_lat,max_lon,max_lat` |
| `page` / `page_size` | `1` / `100` | Pagination (max 1000) |

## Project structure

```
changesets/
  models.py          # Changeset + SequenceState models
  views.py           # Dashboard, API, and import views
  serializers.py     # DRF serializer
  urls.py            # /api/ URL patterns
  osm_fetcher.py     # Fetches and parses OSM replication XML
  management/commands/poll_sequences.py  # Continuous poller
  templates/changesets/
    dashboard.html   # HTML shell — sets window.dashboardData only
    changesets.html  # Import helper UI
osm_changeset_api/
  urls.py            # Root URL conf
  settings.py
static/
  js/dashboard.js    # All Chart.js chart initialisation
  output.css         # Compiled Tailwind CSS
```

See `CLAUDE.md` for architecture decisions and AI-assistant context.
