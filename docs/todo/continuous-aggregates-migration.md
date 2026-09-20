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
