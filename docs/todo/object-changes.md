# Object changes from the minutely diffs ("tier 2")

Architecture agreed 2026-10-03, not started. The changeset feed only gives `changes_count`; the
minutely osmChange diffs (`planet.osm.org/replication/minute/`) give every object version
uploaded, with its changeset id. This item ingests them going forward. A history backfill (full
history planet with osmium, "tier 3") is a separate, later project.

## What the feed gives (measured 2026-10-03)

- **Volume:** ~4.6M object versions a day (last 7 days of `changes_count`), ~48K changesets. The
  daily diff is ~105 MB gzipped, so the minutely files add up to about the same per day.
- **Each element:** `<create>`/`<modify>`/`<delete>` blocks of nodes, ways and relations, each with
  `id`, `version`, `changeset`, `timestamp`, user, and the **new** version's tags (ways: node refs,
  relations: members).
- **No before state:** deletes carry no tags, and a modify doesn't say what changed. Tag diffs need
  the previous version, which only the per-object window below (or a history backfill) has.
- **Built-in check:** per changeset, creates + modifies + deletes must equal its `changes_count`
  in `changesets`. Use it as the parity check for the whole pipeline.

## Decisions

1. **Both granularities.** Per-changeset counts (what other OSM stats tools provide) kept for all
   time, plus one row per object version for a rolling **3 months** (TTL), to measure whether it
   scales before going longer. `object_versions` keeps **everything** the diff has for an element:
   tags, and geometry (node coordinates, way node lists, relation members).
2. **Feature classification from the new version's tags:** each element gets one `feature`, its
   main tag key from a fixed list (`building`, `highway`, `amenity`, `shop`, `landuse`, `natural`,
   `waterway`, `railway`, `power`, `barrier`, `leisure`, `tourism`, …, then `addr` for
   address-only, `other`), `untagged` (mostly way geometry nodes) and `unknown` (deletes). A fixed
   list keeps the number of distinct values bounded. Overpass augmented diffs (with before state)
   were rejected: an external service to depend on.
3. **Minutely, not daily:** the dashboard is near-live, and catch-up uses the same batching as the
   changeset poller.
4. **ClickHouse only.** TimescaleDB is deprecated; the new methods raise `NotImplementedError`
   there (API 501).

## Architecture

**Ingestion: a new `poll_diffs` compose service**, same pattern as `poll_sequences`: its own
minute-sequence state row, catch-up batches, `statement_timeout`, the 512 MB limit, Datadog like
the poller. Streaming parse (`iterparse`, clearing elements) so memory stays flat. Both tables
written from the same parse, in one batch per sequence range.

**`changeset_objects`**, kept forever:
`(changeset_id, sequence, edit_day, node_c, node_m, node_d, way_c, way_m, way_d, rel_c, rel_m, rel_d)`.
Plus **`changeset_features`**: `(changeset_id, sequence, type, action, feature, count)`. A changeset
can span several minutely files, so rows are partial and summed at query time. Keyed by
`(changeset_id, sequence)` with ReplacingMergeTree, so replaying a sequence replaces its rows
instead of double counting (the idempotency rule of `changesets/ingest/writers/base.py`).

**`object_versions`**, 3 months:
`(type, id, version, action, changeset_id, timestamp, user, uid, feature, tags Map(String, String),
lat, lon, node_refs Array(UInt64), members Array(Tuple(type, ref, role)))`, TTL on `timestamp`.
Gives churn (objects re-edited within a day), most re-edited objects, edit wars (the same object
toggling between contributors), and exact tag and geometry diffs between consecutive versions
inside the window. Keeping everything (decided 2026-10-03) makes it the biggest table by far: the
prototype measures it per column, so the window length is set from real numbers.

**Dates and filters:** edits are attributed to their changeset's `created_at` day, so totals add
up to the existing "objects changed" numbers; the date range and the dimension filters (editor,
country, …) come from joining `changesets` on `changeset_id`. For long ranges, a refreshable daily
rollup does that join (watermark + raw after it, like `daily_rollup`). Its cost over full history
is unmeasured.

**API:** parameters on existing endpoint shapes before new endpoints (e.g.
`timeseries?metric=objects&group_by=action`, `toplist?dimension=feature`); churn likely needs a
`largest`-style list. Widgets on the Objects page, with a "data since <date>" note: coverage
starts when the poller starts.

## Prototype results (2026-10-03)

One day of minutely files (sequences 7310994-7312542, 2026-10-02 00:00 to 2026-10-03 02:15 UTC,
1,549 files, 115 MB) parsed into scratch tables (`osm_proto` database; script, schema and files in
`~/osm-monitor-data/diffs-prototype/`, outside the repo). The schema tested is the one above, with
`object_versions` partitioned by day and ordered by `(type, id, version)`.

- **Correct:** for all 51,291 changesets opened on Oct 2 and closed in the window, creates +
  modifies + deletes equal `changes_count` exactly (4,492,269 objects on both sides).
- **Volume:** 5.08M object versions in 26 h (4.54M nodes, 530K ways, 17K relations; 3.50M
  creates, 683K modifies, 900K deletes); 70.6K `changeset_objects` and 161K `changeset_features`
  rows (several partial rows per changeset, one per file it spans).
- **Disk, everything kept:** `object_versions` 68 MB for the 26 h, **14 bytes per version**
  compressed (467 MB uncompressed). Biggest columns: `lon` 15.9 MB and `lat` 15.6 MB (coordinates
  barely compress), `node_refs` 12.9 MB, `tags` 7.1 MB, `id` 7.0 MB, `members` 5.2 MB. That's
  about **5.7 GB for 3 months, 23 GB a year**. The count tables are ~2 MB a day (~0.7 GB a year).
  Disk is not what limits the window.
- **Parse:** 262 s for the day in one Python process (stdlib `iterparse`, no lxml), inserts 32 s;
  peak memory 308 MB with 30-file batches, so batch by row count to stay well under the 512 MB
  limit. Live, that's ~0.2 s a minute.
- **Queries on one day:** most re-edited objects 1.5 s, share of re-edited objects 0.6 s (57K of
  5.0M objects edited more than once in the day, 8.6K by more than one user), tag keys added /
  removed / changed between consecutive versions 0.6 s (top: `tactile_paving`, `surface`,
  `lane_markings`, `maxspeed:hgv` removals, `cycleway:both`). Over 3 months these would take
  minutes, and grouping ~450M rows by object would hit ClickHouse's 4 GB memory cap: **query cost
  is what limits the window**, so the API reads daily rollups (re-edits, tag-key changes per day),
  never `object_versions` directly over a long range.
- **Download:** via the planet.osm.org redirect, 2.6 s a file (67 min for the day); straight from
  the S3 mirror (`osm-planet-eu-central-1.s3...`) over one reused connection, 0.47 s. The poller
  should do the latter.
- **Daily files keep every version:** the Oct 2 daily diff has 4,494,004 elements, the minutely
  files 4,494,005 for the same period (the difference is the midnight boundary). So the backfill
  reads ~90 daily files (~100 MB each) instead of ~130K minutely ones. They have no minute
  sequence: the backfill needs its own idempotency key (e.g. the day sequence in a separate
  range) and must stop where the minutely poller starts, so no day is counted twice.

## Plan

1. ~~Prototype and measure~~ (done, above).
2. Poller and tables (real schema, from the prototype's), then backfill 3 months from the daily
   files and follow the minutely ones from there.
3. Daily rollups (counts by changeset dimensions; re-edits and tag-key changes from
   `object_versions`), API (with `@extend_schema`), parity check against `changes_count`.
4. Objects page widgets.
5. Revisit the window: disk allows a year or more; the rollups' refresh time decides.

Sample seen in one minute (2026-10-03 01:14-01:15 UTC): way 148066277 modified 4 times in 15
seconds by 4 changesets (v12 adds `surface`, v13 `cycleway:both`, v14 `lane_markings`), one quest
answer per changeset. Per-changeset counts show four unrelated "1 way modified" rows; only the
per-object table shows it's one object.
