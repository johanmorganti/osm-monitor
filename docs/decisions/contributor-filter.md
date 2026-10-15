# Contributor filter: lower(user) and its skip index

Decided 2026-10-15, after contributor-filtered dashboard requests were cancelled at the 30 s
query cap (Datadog APM, 2026-10-14).

## What the filter is

The analytics API matches a contributor case-insensitively: `_Query` in
`changesets/analytics/clickhouse/backend.py` (~line 117) adds
`lower(user) = lower({contributor:String})`, like every other dimension filter. `daily_rollup`
has no contributor dimension (too many distinct values), so a contributor-filtered query always
reads the raw `changesets` table.

`schema/0007_user_index.sql` adds `idx_user_lower`, a `bloom_filter` index on **`lower(user)`**.
A skip index is only used when the query contains its exact expression, so an index on plain
`user` would never be used by this filter. Changing the filter expression silently disables the
index: change both together.

## Evidence

- Before: `contributor=wielandb`, 2018-10-14 to 2026-10-14, read 127M rows per query
  (17-30 s each, `FINAL`); a dashboard load runs several at once and some were cancelled.
- Granules a contributor appears in over that range (15,681 in total): wielandb (30k changesets)
  2,245; Lumikeiju (10k) 2,066; Hintersinne (293) 72. So the index saves ~7× for prolific
  contributors and ~200× for occasional ones.
- After materializing: wielandb reads 2,358 of 16,245 granules; the count takes 4.5 s, the
  per-day series 3.1 s; through the API (one request at a time) the timeseries took 6.8 s and the
  Lumikeiju geo request 9.2 s, where both had been cancelled before.

## Limits found along the way

- **ClickHouse's `lower()` only folds ASCII letters**: `lower('ÉMILE Ö')` is `'Émile Ö'`
  (`lowerUTF8` gives `'émile ö'`). So the matching is case-insensitive for ASCII names only. Any
  change here is a public-API behavior change (who matches `contributor=…`), decided on its own,
  and it has to move the index expression with it.
- **The raw list endpoint is inconsistent**: `/api/changesets/` (`ChangesetList`, backend.py
  ~line 747) matches `user = {user:String}` exactly. It differs from every other endpoint and
  doesn't use `idx_user_lower`.
- **Case-sensitive matching would not be faster**: what costs time is how many granules hold the
  contributor's rows, and the table is ordered by `created_at`, so a prolific contributor is in
  the same granules whether the comparison folds case or not. The bloom filter is as selective on
  `lower(user)` as it would be on `user`.
- **A large speedup needs data ordered by contributor**: a rollup or projection sorted by
  contributor, keyed on `user_id` (names change, ids don't). The table is a ReplacingMergeTree
  queried with `FINAL`; whether ClickHouse uses a projection under `FINAL` has to be checked first,
  otherwise a refreshable rollup is the way (see `TODO.md`).
- The bloom index does nothing for the **unfiltered** contributor ranking (top contributors over
  all time: 19 s alone, so a busy server can still cancel it). That needs the same
  contributor-ordered rollup.

## Operations

`ADD INDEX` covers only parts written or merged afterwards; existing rows needed a one-off
`ALTER TABLE changesets MATERIALIZE INDEX idx_user_lower` (run 2026-10-15, 407 parts, see
`docs/DEPLOYMENT.md`).
