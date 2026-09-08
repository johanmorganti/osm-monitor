# TODO — known issues & deferred design work

Running backlog of things found during development but not yet fixed, plus the
reasoning behind them. Keep this current: when something here gets fixed, move
it out (delete the entry, or fold the resolution into CLAUDE.md's architecture
decisions if it's worth remembering *why*); when something new is deferred,
add it here rather than letting it live only in conversation history.

## Known issues (deferred)

### Table partitioning / TimescaleDB
Most real usage of this API is time-filtered (dashboard defaults, `/api/changesets/` 24h window,
etc.), so monthly range partitioning on `created_at` (native Postgres, or via a TimescaleDB
hypertable) would let those queries prune to just the relevant partition(s) instead of searching
an index across the whole table — increasingly important as history grows toward the full
2005-present import (~190M+ rows). TimescaleDB specifically is also attractive because its
continuous aggregates could replace the hand-rolled `DailyVolume`/`DailyBreakdown` rollup system
in `changesets/rollups.py` entirely. Neither helps queries that inherently need every row
regardless of date (e.g. the `FilterValue` backfill) — partition pruning only kicks in when a
query filters by the partition key. Deferred deliberately: converting the *existing* ~23M-row
live table (continuously written by the poller) to either is real migration work, not a quick
add, and shouldn't be done without dedicated planning. Plan floated: benchmark both options
side by side on a separate, beefier machine using the past-year data we already have locally
(the dump file), before deciding; if compared on this machine too, run the two tests
sequentially, never simultaneously — it's resource-constrained enough already (see mem_limit
notes in docker-compose.yml).

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
