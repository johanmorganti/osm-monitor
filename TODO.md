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

### Single gunicorn worker on `web`
No `--workers` flag is set, so `web` runs exactly one sync worker. Any single slow/heavy request
blocks every other request until it finishes or hits gunicorn's 30s worker timeout and gets
SIGKILLed — and the query it was running often keeps executing server-side on Postgres as an
orphaned backend afterward (client gone, server hasn't noticed yet), silently consuming
CPU/IO and sometimes blocking *other* queries behind a lock it's still holding. This caused
several confusing incidents this session before being traced back to the same root cause each
time. Worth adding more workers (`--workers N`) and/or a statement_timeout on the DB connection
so a slow query can't outlive the request indefinitely.

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
