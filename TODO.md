# TODO — known issues & deferred design work

Running backlog of things found during development but not yet fixed, plus the
reasoning behind them. Keep this current: when something here gets fixed, move
it out (delete the entry, or fold the resolution into CLAUDE.md's architecture
decisions if it's worth remembering *why*); when something new is deferred,
add it here rather than letting it live only in conversation history.

## Known issues (deferred)

### TimescaleDB continuous aggregates (replace the hand-rolled rollup system)
`changesets_changeset` is now a TimescaleDB hypertable (see `docs/ARCHITECTURE.md`) — done
deliberately as "hypertable only," with continuous aggregates as an explicit follow-up rather
than bundled into the same pass. `DailyVolume`/`DailyBreakdown` + `refresh_rollups_incremental()`/
`refresh_rollups_reconcile()` (`changesets/rollups.py`) still exist and work, but are exactly the
kind of hand-rolled machinery Timescale's continuous aggregates would replace with something
battle-tested. Concrete motivating incident: right after the migration, the incremental refresh
tried to process the *entire* freshly-bulk-imported dataset (millions of rows) as one pass — the
wrong tool for populating from scratch, and it ran for 90+ minutes before being killed in favor of
a one-time `refresh_rollups()` full rebuild instead. A continuous aggregate's refresh policy is
designed to handle both the bulk-backfill and steady-incremental cases correctly out of the box.
Worth scoping as its own piece of work (needs a design pass on what the aggregates should look
like and how `TimeseriesView`/`SummaryView`/`ToplistView`'s rollup-path queries change), not
something to bolt on to an already-eventful deploy.

Also the place to fix a related, currently-open gap: a `contributor`/`editor`/`imagery` filter on
`SummaryView`/`ToplistView` always falls back to scanning raw `Changeset` rows
(`_filtered_changesets` in `changesets/views.py`), which is fine over a week but times out over a
full year (confirmed: `summary/?editor=X` over a year hit gunicorn's 30s worker timeout and left
an orphaned Postgres backend running for 2+ minutes afterward, resistant to `pg_cancel_backend`).
`DailyBreakdown` already stores per-name-per-day rows for exactly these three dimensions, so a
*single*-dimension filter could be served from there instead — deliberately not hand-rolled onto
the existing rollup tables now, in favor of building the equivalent as a continuous aggregate when
this item gets picked up.

### Compression backlog not yet compressed — policy manually paused
`changesets_changeset` has compression enabled (`0019_compress_changesets`, segmented by
`created_by_family`/editor, 30-day policy) but the policy job (job_id 1000) is currently
**unscheduled** (`SELECT alter_job(1000, scheduled => false);`) and only one chunk is actually
compressed. First activation tried to compress the entire ~13-month backlog (12 real chunks,
~11GB) in one call — the same "processes the whole backlog in one pass" trap as the rollup
incremental-refresh incident — and was killed after 18+ minutes to compress a single 84MB chunk
while actively starving other queries (a poller rollup insert stuck 12+ minutes). Deliberately
left paused rather than worked through gradually; re-enabling needs either a controlled
one-chunk-at-a-time manual pass or accepting a similar I/O spike. Before doing that backlog pass,
reconsider `compress_segmentby`: multiple columns are supported (editor ~778 distinct, imagery
~773 distinct — both fine cardinality-wise for segment-exclusion benefit on either filter
independently), and a future `country` filter (extrapolated from bbox) could join it once that's
a real stored column (segmentby requires an actual column, not an expression) — cheaper to decide
the final column list once, before paying the backlog compression cost, than to compress now and
redo it later.

### Datadog log pipeline severity remapping
Postgres `LOG:`-level lines are showing up in Datadog with `status:error`. Cosmetic/noisy, not
a functional bug. Needs a manual fix in the Datadog UI (Logs → Pipelines) — no MCP tool access
to do this one programmatically from here.

## Planned work

### Favicon
The dashboard has no favicon — confirmed by recurring `Not Found: /favicon.ico` 404s in `web`'s
logs all session. Needs an actual design, not just a placeholder.

## Design principles for future work

- **Design for the full history import, not just the current subset.** Only the past year is
  imported today (~23M rows), but the eventual goal is full 2005-present history (~190M+ rows,
  ~8x bigger). Before calling a query/index/background-job design "done," sanity-check it against
  "does this still work at 8x the row count." Concrete example of what happens when that check
  gets skipped: `refresh_rollups()`'s old hourly full `TRUNCATE`+rebuild-from-everything already
  took 11+ minutes at 23M rows, locking the dashboard out for the duration each time — replaced
  with `refresh_rollups_reconcile()`, which only recomputes a bounded recent window (see
  `changesets/rollups.py`'s module docstring) since that's all that can ever actually be stale.
  "Simple" means few moving parts, not "whatever happens to work on today's data size."
