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
   scales before going longer.
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
`(type, id, version, action, changeset_id, timestamp, feature, tags?)`, TTL on `timestamp`.
Gives churn (objects re-edited within a day), most re-edited objects, edit wars (the same object
toggling between contributors), and exact tag diffs between consecutive versions inside the
window. Whether to keep the full tag map (needed for tag diffs) or only `feature` is the main size
question for the prototype.

**Dates and filters:** edits are attributed to their changeset's `created_at` day, so totals add
up to the existing "objects changed" numbers; the date range and the dimension filters (editor,
country, …) come from joining `changesets` on `changeset_id`. For long ranges, a refreshable daily
rollup does that join (watermark + raw after it, like `daily_rollup`). Its cost over full history
is unmeasured.

**API:** parameters on existing endpoint shapes before new endpoints (e.g.
`timeseries?metric=objects&group_by=action`, `toplist?dimension=feature`); churn likely needs a
`largest`-style list. Widgets on the Objects page, with a "data since <date>" note: coverage
starts when the poller starts.

## Plan

1. **Prototype and measure** (before any schema is final): parse one full day of minutely files
   on this host. Measure download and parse time, rows and compressed bytes per table (with and
   without tags in `object_versions`), and check the per-changeset sum against `changes_count`.
   Extrapolate `object_versions` to 3 months and to a year.
2. Poller and tables, then catch up from the start date.
3. Rollup and API (with `@extend_schema`), parity check.
4. Objects page widgets.
5. Decide whether the per-object window can grow, from the measurements.

Sample seen in one minute (2026-10-03 01:14-01:15 UTC): way 148066277 modified 4 times in 15
seconds by 4 changesets (v12 adds `surface`, v13 `cycleway:both`, v14 `lane_markings`), one quest
answer per changeset. Per-changeset counts show four unrelated "1 way modified" rows; only the
per-object table shows it's one object.
