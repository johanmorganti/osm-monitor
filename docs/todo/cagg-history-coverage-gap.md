# Stats CAggs only cover Aug 2025+, unlike the raw table's growing history

`cagg_volume_hourly`/`cagg_volume_daily` and all four `cagg_{editor,imagery,locale,contributor}_
{daily,hourly}` views only have data from **2025-08-03/12 onward** (confirmed via `min(bucket)` on
each) — they were backfilled once, back when "past year" was the practical scope. The background
historical import has since walked further back (raw `changesets_changeset` goes back to
2005-10-06, in 138 chunks), but nobody re-backfilled these 10 CAggs to match. Result: any unfiltered
dashboard widget (`TimeseriesView`/`SummaryView`/`ToplistView`'s CAgg-backed fast path) silently
returns empty/zero for a date range before ~Aug 2025, even though real data exists there — reproduced
directly: `changesets_changeset` has 8 rows for 2025-07-01..07, but
`/api/changesets/timeseries/?start_date=2025-07-01&end_date=2025-07-07` returns `dates: []`. No
fallback to the raw table exists for this case (unlike the filtered path, which already falls back).

`cagg_geo_daily` (the old geo heatmap CAgg, retired 2026-09-19 — see `CLAUDE.md`'s "Geo storage: a
single geohash key" section) did NOT have this gap in date-range terms — it was backfilled for the
full 2005-2026 range from the start — though its replacement's backfill incidentally turned up one
row it was silently missing within that range (same underlying mechanism as this doc describes,
just a single row rather than a whole unmaterialized range).

Fix, when this matters: backfill all 10 CAggs to full history the same way (`CALL
refresh_continuous_aggregate(...)`, one chunk at a time) — set aside for now since today's real
usage only queries the past year.

See also `CLAUDE.md`'s "Old-dated rows in the replication stream are normal" section — that entry
covers a related but distinct case (comment-driven old rows arriving via live polling, not a
backfill gap) and should not be confused with this one.
