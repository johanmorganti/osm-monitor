# Architecture decisions

One file per decision: what was decided, why, and the evidence behind it. The rules that follow
from them are summarized in [`../../CLAUDE.md`](../../CLAUDE.md); how the system works today is in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## API and pages

- [Template / JS separation](template-js-separation.md): HTML shells, all data fetched from the public JSON API.
- [Endpoints split by shape](api-endpoint-shapes.md): timeseries / summary / toplist instead of one bundled endpoint; widgets load lazily.
- [Dimension naming](dimension-naming.md): DB column vs. API param vs. UI label, and the mapping for every dimension.
- [NULL tags get a "(none)" bucket](none-bucket.md): untagged volume is never silently excluded.

## Analytics and storage

- [Analytics backends](analytics-backends.md): the API asks, a backend answers; ClickHouse and its rollups.
- [TimescaleDB and Postgres removed](timescale-removal.md): ClickHouse holds everything; the app's state is a SQLite file (tested on the shared folder first).
- [Design for full history](full-history.md): ~190M+ rows, 2005 to today; watermark on `created_at`.
- [Old-dated rows in the replication stream are normal](old-dated-rows.md): comments resurface old changesets; why the 7-day refresh window is right.
- [Location data computed at ingest](location-at-ingest.md): geohash and country in Python, identical for every backend.
- [Geo storage: one geohash key](geo-geohash.md): prefix nesting for every zoom level, and six lessons from shipping it.
- [TimescaleDB storage](timescale-storage.md) (history, removed 2026-10-05): hypertable, compression, bloom filters, filter by equality.
- [imagery_used as a JSON array](imagery-used-json.md)
- [SequenceState](sequence-state.md): the poller's resume point.
- [Changesets the feed leaves open](stale-open-changesets.md): re-fetched from the OSM API when still open 25 h after creation; writers replace an open copy with a closed one.
- [Object changes](object-changes.md): per-changeset counts forever and every object version for 92 days, from the replication diffs; minute/day files meet without overlap.

## Operations

- [Statement timeout](statement-timeout.md) (history, Postgres removed 2026-10-05): bounded by default; long jobs opted out explicitly.

## Project

- [Documentation layout](documentation-layout.md): CLAUDE.md for agents, decisions here, TODO.md as an index.
