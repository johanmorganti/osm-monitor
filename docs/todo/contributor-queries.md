# Contributor queries: contributor-ordered data

Follow-ups from [contributor-filter](../decisions/contributor-filter.md) (2026-10-15), where the
`lower(user)` bloom index fixed the cancelled contributor-filtered requests.

Still slow: the unfiltered contributor ranking over long ranges (`toplist?dimension=contributor`,
`timeseries?group_by=contributor`, 2005 to today: 19 s alone, cancelled at 30 s on 2026-10-14
when several ran at once), and prolific contributors over many years (wielandb 2018-2026:
3-7 s).

Plan:

1. **Check projections under `FINAL` first.** A projection ordered by `user_id` would serve both
   the filter and the ranking without a new table, but `changesets` is a ReplacingMergeTree read
   with `FINAL`, and the projection is only worth it if ClickHouse uses it there (and with
   deduplication correct). Test on a copy, with `EXPLAIN indexes=1` and `check_backend_parity`.
2. **Otherwise a refreshable monthly rollup** (rules: refreshable, never incremental), e.g.
   `contributor_monthly (month, user_id, user, changesets, objects)`, keyed on `user_id` (names
   change; carry the latest name). Measured 2026-10-15: 7.9M (month, user) rows against 190.6M
   changesets (daily would be 28M). Queries read whole months inside the range from the rollup up
   to its watermark, and the raw table for the partial months at both ends and after the
   watermark, like `daily_rollup`.
3. Measure the full-history ranking before and after; snapshot the API calls and diff the OpenAPI
   schema (behavior-neutral).

Where the 19 s goes (measured 2026-10-15, one query alone, `toplist?dimension=contributor` SQL
over 2005 to today): 13.6 s with `FINAL`, 9.8 s without. So `FINAL` adds ~40%, and the rest is
grouping 190.6M rows by 2.6M contributors. Dropping `FINAL` is not an option: 53,679 rows were
waiting for a merge (0.03%, mostly recent open/closed pairs, i.e. the short ranges where a double
count would show). The rollup pays for `FINAL` once per refresh instead of per request.

Side idea, for every `FINAL` query: 112 of 259 monthly partitions had 2-5 parts, because comments
resurface old changesets into old months. Old partitions receive few rows, so an occasional
`OPTIMIZE TABLE changesets PARTITION … FINAL` on them could keep them at one part and make
`FINAL` cheaper there. Measure the gain and the merge cost before scheduling it.

Case sensitivity was decided separately (kept case-insensitive, `lower()`; `/api/changesets/`
aligned): see the decision file.
