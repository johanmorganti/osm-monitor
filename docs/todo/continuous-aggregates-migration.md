# TimescaleDB continuous aggregates — cleanup + one real remaining gap

The old hand-rolled rollup system (`DailyVolume`/`DailyBreakdown` +
`refresh_rollups_incremental()`/`refresh_rollups_reconcile()` in `changesets/rollups.py`) has been
fully replaced by TimescaleDB continuous aggregates (see "Aggregates" in `docs/ARCHITECTURE.md`)
— `views.py` reads only the CAggs now.

Neither ingestion path needs its own CAgg-refresh code for *live* data: every CAgg (including the
3 pair CAggs below) carries its own `add_continuous_aggregate_policy`, a Timescale-managed
background job that refreshes on its own schedule regardless of what inserted the underlying rows
— `poll_sequences.py` (the always-running poller) never touches CAggs at all. `import_from_dump.py`
is the one path that *does* need an explicit call, since a historical bulk import routinely lands
outside every policy's 7-day `start_offset` window (see CLAUDE.md's "Old-dated rows..." section) —
it already makes one, over the imported range, using `cagg_maintenance.ALL_CAGG_NAMES` (confirmed
2026-09-21 still current: adding a new CAgg to that one list, as the pair CAggs below did, is
sufficient — no per-command changes needed).

## Remaining steps

1. Follow-up migration to drop the now-fully-dead old rollup machinery: `DailyVolume`/
   `DailyBreakdown` tables (414 MB + 1 MB), `changesets_changeset_id_idx` (758 MB — nothing queries
   `Changeset.id` directly once the tables above are gone; this index alone projects to ~8 GB at
   full 190M-row history), `RollupState.last_id`, and the three old rollup functions in
   `changesets/rollups.py` (`refresh_rollups()` currently remains callable only as the manual
   `manage.py refresh_rollups` escape hatch). Deliberately separate from an application-code
   change — this is schema surgery on tables/indexes still physically present on disk.

## Real, confirmed remaining gap

Any *cross-dimension* filtered query — a `contributor`/`editor`/`language` toplist filtered by
`imagery` (or vice versa), or `TimeseriesView` with both a `group_by` and a filter set — has no
matching CA (each CA only tracks its own single dimension) and falls back to raw `Changeset`
scanning. Confirmed via direct test: `toplist/?dimension={contributor,editor,imagery,language}
&imagery=Mapbox` over a 3-month range all hit the 30s statement timeout and got cancelled, for
every dimension tried.

Root cause, confirmed via `timescaledb_information.compression_settings`: the hypertable's
compression is `segmentby created_by_family` (editor) only (see
`docs/todo/compression-backlog.md`) — a query that filters or groups by anything *other* than
editor can't exclude compressed segments and ends up decompressing everything in range. Not
fixable by adding more CAs (a CA per dimension *pair* doesn't scale — see `CLAUDE.md`'s "design for
full history" principle); the real fix is either widening `compress_segmentby` (cost: lower
compression ratio, and a backlog-recompression pass) or accepting that cross-dimension-filtered
toplists stay slow/degrade gracefully (e.g. UI disables or caps the range for that combination).
Needs a decision before doing more work here.

**2026-09-20 addendum — reproduces even with zero compression involved.** Root-caused a live
report of `?start_date=2026-03-24&end_date=2026-09-20&language=ES` timing out most of the
dashboard's widgets. Found and fixed a real, separate bug on the way: `locale_family` was missing
the `UPPER()` expression index its sibling dimensions (`user`/`created_by_family`/`imagery_family`)
all got in migration 0015 — that migration predates `language` becoming a dashboard filter, and
nobody added the matching index when it was wired up later. Every `ToplistView`/`GeoView` call
with a `language` filter was forcing a full parallel sequential scan of every chunk (cost ~1.4M).
Fixed in migration 0040 (built per-chunk with `CREATE INDEX CONCURRENTLY`, since TimescaleDB
rejects `CONCURRENTLY` directly against a hypertable — see that migration's comment).

That fix is real (cost dropped ~26x, chunk exclusion works again) but did **not** fully resolve the
report: the identical query shape still exceeds the 30s statement timeout after the index fix, and
the same is true substituting `imagery=Bing` for `language=ES` over the same 6-month range —
confirmed on chunks `timescaledb_information.chunks.is_compressed = false` (this project's
compression backlog has only recompressed 1 of ~13 chunks, so the segmentby explanation above
doesn't apply to what was tested here). Cause: `EXPLAIN` shows a `Bitmap Heap Scan` rather than an
`Index Only Scan` even for a bare `count(*)` — the matched rows (~7-9K per chunk out of chunks
this size) are scattered essentially randomly across each chunk's data pages, since physical
row order is chronological (insertion order) and has no correlation with locale/imagery. Low
selectivity at low physical clustering means the index still has to drive on the order of
thousands of individual random page reads per chunk, and this host's shared/HDD-backed I/O can't
absorb that within 30s regardless of which single dimension is filtered. In other words: this
project's *general* "any one filter + a wide-enough date range falls back to raw scanning" trade-
off (documented above, already accepted for the compression-exclusion reason) has a second,
independent cause that would persist even after a `compress_segmentby` fix — needs to be part of
the same decision, not solved by the index fix alone. A covering/`INCLUDE` index matching a given
toplist's exact `(filter dimension, grouped dimension)` pair would let that one shape become an
Index Only Scan, but that's the same "one index per dimension pair" combinatorial-explosion problem
already rejected above for CAs.

**2026-09-21 — editor/imagery/language now have real pair CAggs; contributor + geo are the
remaining gap.** Reconsidered the "not fixable by adding more CAs" conclusion above: it's true as
a blanket statement across *all* dimension pairs, but not uniformly — the 4 dimensions have very
different cardinalities (confirmed via `count(DISTINCT name)` on each single-dimension CAgg):

| Dimension | Distinct values |
|---|---|
| contributor | 344,304 |
| editor | 824 |
| imagery | 773 |
| language | 121 |

`editor`/`imagery`/`language` are all low-cardinality — a pair CAgg between any two of them is
small and bounded. `contributor` is the outlier: not necessarily explosive in row count (most
contributors stick to ~1 editor/imagery/language, so a contributor-crossed CAgg wouldn't approach
a full cross-product), but every CAgg also carries a *recurring* refresh cost every time its
policy fires (not just a one-time build cost), and this host is already I/O-constrained (see
CLAUDE.md's statement_timeout / VACUUM-crash notes) — 3 more contributor-crossed CAggs would
meaningfully add to that ongoing load.

Built the 3 pairs *not* involving contributor — `cagg_editor_imagery_daily`,
`cagg_editor_locale_daily`, `cagg_imagery_locale_daily` (migration 0041, daily-grain only; models
in `changesets/models.py`; `PAIR_CAGGS`/`_pair_cagg_lookup` in `views.py` wire them into
`ToplistView` and `TimeseriesView`'s `group_by`+filter path). Backfilled from 2025-08-01 (matching
the single-dimension CAggs' own coverage — see `docs/todo/cagg-history-coverage-gap.md`) via the
new `backfill_dimension_pair_caggs` management command, in small 7-day batches (default is smaller
than `refresh_caggs_over_range`'s usual 30 — this host was already showing memory pressure the same
session, see the `--batch-days` flag to widen it later once proven safe).

**What this fixes:** `ToplistView`/`TimeseriesView` calls where the filtered dimension and the
requested `dimension`/`group_by` are two *different* ones of {editor, imagery, language} — e.g. the
original bug report's `toplist?language=ES&dimension=editor` and `&dimension=imagery` calls.

**What's still open** (unchanged from above, now precisely scoped rather than "any cross-dimension
query"):
- **`GeoView`** with any active filter — a different shape entirely (dimension x geohash, not
  dimension x dimension; geo cells aren't a small fixed set of names the way editor/imagery/
  language are), not attempted in this pass. See `GeoView`'s docstring in `views.py`.
- The degenerate case where the filtered dimension *equals* the requested `dimension`/`group_by`
  (e.g. `toplist?language=ES&dimension=language`) — answerable today from the existing
  single-dimension CAgg directly (it's just that one filtered value's own total), but
  `ToplistView`/`TimeseriesView` don't special-case it yet and still fall back to raw scanning.
  Minor; not part of this pass.

**2026-09-22 — contributor pairs built too, despite the cost flagged above.** The
"filter by a common editor/imagery/language, group by contributor" direction (not covered by
`changeset_user_upper_idx`'s selectivity, since the filter there is never on `user`) was confirmed
as the concrete remaining slow path, so built the 3 contributor pairs anyway:
`cagg_contributor_editor_daily`, `cagg_contributor_imagery_daily`, `cagg_contributor_locale_daily`
(migration 0043, same daily-grain-only shape as 0041's three). `PAIR_CAGGS` in `views.py` no longer
excludes contributor — the lookup is fully generic now across all 6 pairs.

The cardinality cost was real, not just theoretical: backfilling the 3 non-contributor pairs
(migration 0041) took a few minutes total; backfilling these 3 took roughly an hour on this
host, confirmed via `pg_stat_activity` mid-run showing individual 7-day batch refreshes taking
30-40s of real `DataFileRead` I/O wait each (vs. sub-second for the non-contributor pairs) — one
batch, not the whole backfill. Reused `backfill_dimension_pair_caggs` (extended `PAIR_CAGG_NAMES`
rather than adding a second command — the command was already generic over "whatever's in this
list"), same 2025-08-01 start and 7-day batches. Verified end-to-end post-backfill: the
originally-slow `toplist?editor=iD&dimension=contributor` and `toplist?language=ES&
dimension=contributor` calls both now return in under a second (previously 30s+ timeouts).

All 4 dimensions are now fully cross-covered (6 pairs = C(4,2)) except `GeoView`, which remains
the one open item above.
