# Object changes from the replication diffs: longer ranges, window

Built (2026-10-03): `poll_diffs` (the `diff-poller` service) writes per-changeset counts
(`object_changes`, `object_change_features`, all time), every object version
(`object_versions`, 92 days) and edits to existing objects (`object_edits`, all time) to
ClickHouse; `/api/objects/most-edited/` ranks the last 7 days; `object_daily_rollup` is built
one day at a time; `object_daily_rollup` serves
`group_by`/`dimension=action|object_type|feature` on `timeseries`/`toplist`, shown in the Objects
page's "What was changed" section. Design, measurements and checks are in
[`decisions/object-changes.md`](../decisions/object-changes.md).

## Remaining steps

1. **Edits over longer ranges**: "Most Edited Objects" is the last 7 days only (ranking over 30+
   days is too slow, see the decision file). If longer ranges matter: a weekly rollup of objects
   edited at least twice in a week gives the exact top N whenever the Nth object has more edits
   than the range has weeks. Also possible from `object_edits`: re-edits per day (same vs another
   contributor).
2. **Features Over Time colors** follow rank among the top 6 features, so a feature's color can
   change with the range; give features a fixed order if that confuses.
3. **The 92-day window** of `object_versions` can grow if needed: ~26 bytes per version (8.6 GB
   for 92 days, ~34 GB a year), and disk isn't the constraint (261 GB free on 2026-10-04).
4. Later, separate project: history backfill from the full history planet (osmium), which also
   has the previous version of every object.

## Notes

Sample seen in one minute (2026-10-03 01:14-01:15 UTC): way 148066277 modified 4 times in 15
seconds by 4 changesets (v12 adds `surface`, v13 `cycleway:both`, v14 `lane_markings`), one quest
answer per changeset. Per-changeset counts show four unrelated "1 way modified" rows; only the
per-object table shows it's one object.
