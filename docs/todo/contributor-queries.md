# Contributor queries: ordering, consistency, case

Follow-ups from [contributor-filter](../decisions/contributor-filter.md) (2026-10-15), where the
`lower(user)` bloom index fixed the cancelled contributor-filtered requests.

## 1. Contributor-ordered data (speed)

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

## 2. `/api/changesets/` matches the contributor exactly

`ChangesetList` (backend.py ~line 747) uses `user = {user:String}`: case-sensitive, unlike every
other endpoint, and it doesn't use `idx_user_lower`. Aligning it changes who matches on a public
endpoint, so it goes with item 3.

## 3. Case sensitivity (public-API decision)

`lower()` folds only ASCII letters, so `contributor=émile` doesn't match `Émile`. Options:
`lowerUTF8` everywhere (index on `lowerUTF8(user)`, rematerialized), exact match everywhere
(consistent, but a breaking change for callers relying on case-insensitivity), or leave it.
Neither choice changes the speed (see the decision file). Whatever is chosen, the filter
expression and the index expression move together.
