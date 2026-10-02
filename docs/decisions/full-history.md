# Design for full history

The target dataset is full 2005-present OSM changeset history (~190M+ rows, growing), not a recent
subset — a deployment may hold less (e.g. only the poller's default 365-day backfill), but code
must not assume it. When adding a query, index, or background job, sanity-check it against the
full-history row count and chunk count rather than whatever dataset it was developed against.
Two concrete examples already hit by this project: the old `DailyVolume`/
`DailyBreakdown` rollup refresh loop was watermarked on `id` (not the hypertable's partitioning
column), so it got no chunk exclusion and its cost grew with total chunk count regardless of how
much data had actually changed — measured at ~15% of total DB time before being
replaced by the CAgg design (see "Aggregates" in `docs/ARCHITECTURE.md`); `TimeseriesView`'s
bucket-width picker (`pick_interval` in `changesets/api/params.py`, migration `0023`) only offers hour/day grains — daily is
~400 points over a year but thousands over multi-year ranges, so a weekly/monthly tier is the
natural next step once multi-year ranges are common.
Prefer incremental/watermark-based designs over periodic full-table rebuilds, and watermark on the
hypertable's partitioning column (`created_at`) specifically, not a surrogate key like `id`.
