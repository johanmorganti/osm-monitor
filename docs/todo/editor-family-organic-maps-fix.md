# Editor family: `Organic` should be `Organic Maps` (one-character bug)

`_parse_changeset_element` in `changesets/osm_fetcher.py` had `family == 'Organic Maps'` — a
comparison, not an assignment — where every neighbouring special case uses `=`. The remap
therefore never happened and Organic Maps changesets are filed under the truncated `Organic`.
**The code is fixed (2026-09-18); the already-imported rows are not.**

Verified against raw replication data before fixing: cached sequence files contain
`created_by=Organic Maps android 2026.08.27-18-Google`, which takes no early branch and so hits
`split(' ')[0]` → `Organic`. Every distinct raw `created_by` currently stored under family
`Organic` is a genuine Organic Maps string (`android`/`ios`, assorted versions) — **no false
positives, so a blanket remap is safe**.

Scope, from `cagg_editor_daily` (instant; the equivalent raw-table scan had to be cancelled after
2+ minutes): **209,579 rows**, spanning 2025-08-17 to 2026-09-17 — i.e. effectively the whole
dataset, ~1.2% of it.

## Remaining work

1. `UPDATE ... SET created_by_family = 'Organic Maps' WHERE created_by_family = 'Organic'`, done
   **chunk-by-chunk / month-by-month**, not as one statement — 209K row versions plus WAL on an
   HDD is a real write batch. The `centroid` trigger is `UPDATE OF min_lat, max_lat, min_lon,
   max_lon`, so it will not fire for this update.
2. The matching `changesets_filtervalue` row (`field='editor', value='Organic'`).
3. Refresh `cagg_editor_daily` and `cagg_editor_hourly` over the affected range — 13 months, far
   outside the 7-day policy window, so this needs explicit
   `CALL refresh_continuous_aggregate(...)` calls per month (~14 of them). Same mechanism as the
   backfill caveat in `CLAUDE.md`'s replication-stream section.

Worth checking while in here: `StreetComplete_ee` (a fork) currently folds into `StreetComplete`
via the `startswith` branch. That may well be intended — but it's the same class of decision and
nobody has stated which behaviour is wanted.
