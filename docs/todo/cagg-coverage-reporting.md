# Aggregate endpoints return empty/zero for date ranges the CAggs haven't materialized

The unfiltered fast path of `TimeseriesView`/`SummaryView`/`ToplistView` reads the stats CAggs
(`cagg_volume_*`, `cagg_{editor,imagery,locale,contributor,country}_*`, the pair CAggs). A CAgg only
has data for ranges that have actually been refreshed: its policy covers the last 7 days, and
anything older is only materialized by an explicit refresh (`import_from_dump` at the end of a run,
or `refresh_caggs <start> <end>`). If raw rows exist for a range that was never refreshed, e.g. a
bulk import run with `--skip-cagg-refresh` and no follow-up `refresh_caggs`, or the rare
comment-driven old rows described in `CLAUDE.md`'s "Old-dated rows in the replication stream are
normal" section, the endpoints silently return `dates: []`/`0`, indistinguishable from "no
editing happened". There is no fallback to the raw table on this path (unlike the filtered path).

Fix options, when this matters:
- Report coverage explicitly: expose the materialized range (e.g. `min(bucket)` per CAgg, cached)
  and have the API/dashboard say "no coverage" for ranges outside it instead of `0`.
- Or fall back to the raw table for ranges outside coverage. That's only viable for narrow ranges
  given full-history row counts.

See also `CLAUDE.md`'s "Old-dated rows in the replication stream are normal" section, which covers
the related comment-driven case and why widening the CAgg policies' refresh window is not the fix.
