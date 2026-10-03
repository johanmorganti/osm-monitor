# Object changes from the replication diffs: re-edits, tag changes, upkeep

Built (2026-10-03): `poll_diffs` (the `diff-poller` service) writes per-changeset counts
(`object_changes`, `object_change_features`, all time) and every object version
(`object_versions`, 92 days) to ClickHouse; `object_daily_rollup` serves
`group_by`/`dimension=action|object_type|feature` on `timeseries`/`toplist`, shown in the Objects
page's "What was changed" section. Design, measurements and checks are in
[`decisions/object-changes.md`](../decisions/object-changes.md).

## Remaining steps

1. **Check the first backfill**: when it completes, compare each day's per-changeset sums with
   `changes_count` (see the decision file), and measure real disk use against the ~63 MB a day
   estimate.
2. **Re-edits and tag changes** from `object_versions`: daily rollups of objects re-edited (by one
   or several users) and of tag keys added / removed / changed, then API parameters and widgets.
   Measured on one day: 0.6-1.5 s each; over 92 days these must be rollups, never ad-hoc queries
   (memory cap). A list of the most re-edited objects is a new resource (objects, not
   changesets), so likely its own endpoint.
3. **Bound the rollup refresh** before `object_change_features` reaches about a year: it's rebuilt
   from the whole table daily (9 s / 1.4 GB for 52 days).
4. **Features Over Time colors** follow rank among the top 6 features, so a feature's color can
   change with the range; give features a fixed order if that confuses.
5. **Revisit the 92-day window** once the rollups' refresh time is known. Disk: 14 bytes per
   version measured after a full merge on one day (~23 GB a year), but ~23 bytes per version
   mid-backfill (5.57 GB for 52 days); step 1 gives the settled number.
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
