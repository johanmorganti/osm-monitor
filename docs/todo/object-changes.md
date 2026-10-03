# Object changes from the replication diffs: rollups, API, widgets

Ingestion is built (2026-10-03): `poll_diffs` (the `diff-poller` service) writes per-changeset
counts (`object_changes`, `object_change_features`, all time) and every object version
(`object_versions`, 92 days) to ClickHouse. Design, measurements and checks are in
[`decisions/object-changes.md`](../decisions/object-changes.md). Nothing reads the tables yet.

## Remaining steps

1. **Check the first backfill**: when it completes, compare each day's per-changeset sums with
   `changes_count` (see the decision file), and measure real disk use against the ~63 MB a day
   estimate.
2. **Daily rollups** (refreshable, watermark + raw after it, like `daily_rollup`):
   - counts by changeset dimension (editor, country, …) × type × action × feature, which needs
     the join to `changesets` on `changeset_id` (edits attributed to the changeset's `created_at`
     day, so totals add up to the existing "objects changed"). The join's refresh cost over the
     whole count tables is unmeasured.
   - from `object_versions`: objects re-edited (by one or several users) and tag keys added /
     removed / changed per day. Measured on one day: 0.6-1.5 s each; over 92 days these must be
     rollups, never ad-hoc queries (memory cap).
3. **API**: parameters on existing endpoint shapes first (e.g.
   `timeseries?metric=objects&group_by=action`, `toplist?dimension=feature`), `@extend_schema`
   updated, and a parity check against `changes_count`.
4. **Objects page widgets**, with a "data since <date>" note (counts start 92 days before the
   poller's first run).
5. **Revisit the 92-day window** once the rollups' refresh time is known: disk allows a year or
   more (~23 GB).
6. Later, separate project: history backfill from the full history planet (osmium), which also
   has the previous version of every object.

## Prototype notes (2026-10-03)

Scratch tables in the `osm_proto` ClickHouse database and files in
`~/osm-monitor-data/diffs-prototype/` (one day of minutely diffs and the 2026-10-02 daily diff):
test data, to delete once the real tables are checked (step 1).

Sample seen in one minute (2026-10-03 01:14-01:15 UTC): way 148066277 modified 4 times in 15
seconds by 4 changesets (v12 adds `surface`, v13 `cycleway:both`, v14 `lane_markings`), one quest
answer per changeset. Per-changeset counts show four unrelated "1 way modified" rows; only the
per-object table shows it's one object.
