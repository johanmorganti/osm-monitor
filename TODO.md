# TODO — known issues & deferred design work

Running backlog of things found during development but not yet fixed, plus the
reasoning behind them. Keep this current: when something here gets fixed, move
it out (delete the entry, or fold the resolution into CLAUDE.md's architecture
decisions if it's worth remembering *why*); when something new is deferred,
add it here rather than letting it live only in conversation history.

## Known issues (deferred)

### Autocomplete filter inputs slow at current scale
`AutocompleteView` (`/api/autocomplete/`) backs the dashboard's contributor/editor/imagery
search-as-you-type inputs using `__icontains` (`user__icontains`, etc.). That compiles to
`ILIKE '%q%'` on Postgres, which can't use a plain btree index — it's a full sequential scan,
and now slow/timing out at ~23M rows. Needs a real fix, not a quick patch: a `pg_trgm` GIN index
would make `ILIKE` fast, or (probably better long-term, see the scaling note below) a small
precomputed "distinct known values" table per field, refreshed by the poller alongside the
rollups, so autocomplete never touches `Changeset` directly regardless of table size.

### `refresh_rollups()` full rebuild doesn't scale to full history
`changesets/rollups.py`'s `refresh_rollups()` does a `TRUNCATE` + full-table-scan rebuild of
`DailyVolume`/`DailyBreakdown` from `Changeset`, on an hourly timer (`rollup_full_interval`).
Already took 11+ minutes at ~23M rows, blocking all reads of those tables for the duration (the
`TRUNCATE` takes an exclusive lock) — including the dashboard's default view and, transitively,
live polling (single-threaded poll loop). At the eventual full 2005-present import (~190M+ rows)
this will not finish inside its own interval and will effectively wedge the dashboard
indefinitely whenever it runs. Needs a redesign that stays incremental-only — e.g. drop the full
rebuild entirely and instead have the incremental path reconcile a bounded recent window (last
N days) on its own cadence, rather than ever re-scanning the whole table.

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

## Design principles for future work

- **Design for the full history import, not just the current subset.** Only the past year is
  imported today (~23M rows), but the eventual goal is full 2005-present history (~190M+ rows,
  ~8x bigger). Before calling a query/index/background-job design "done," sanity-check it against
  "does this still work at 8x the row count" — see the `refresh_rollups()` item above for what
  happens when that check gets skipped. "Simple" means few moving parts, not "whatever happens
  to work on today's data size."
