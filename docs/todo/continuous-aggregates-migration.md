# TimescaleDB continuous aggregates — cleanup + one real remaining gap

The old hand-rolled rollup system (`DailyVolume`/`DailyBreakdown` +
`refresh_rollups_incremental()`/`refresh_rollups_reconcile()` in `changesets/rollups.py`) has been
fully replaced by TimescaleDB continuous aggregates (see "Aggregates" in `docs/ARCHITECTURE.md`)
— `views.py` reads only the CAggs now. Two things from that migration are still open:

## Remaining steps

1. Add auto-refresh to `import_from_dump.py` (track first `created_at` seen, `CALL
   refresh_continuous_aggregate` over the imported range at the end of the run) — otherwise a
   future bulk historical import lands outside every CA policy's `start_offset` window and stays
   silently unmaterialized until someone remembers to backfill by hand.
2. Follow-up migration to drop the now-fully-dead old rollup machinery: `DailyVolume`/
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
