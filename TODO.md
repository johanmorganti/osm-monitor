# TODO — known issues & deferred design work

Running backlog of things found during development but not yet fixed, plus the
reasoning behind them. Keep this current: when something here gets fixed, move
it out (delete the entry, or fold the resolution into CLAUDE.md's architecture
decisions if it's worth remembering *why*); when something new is deferred,
add it here rather than letting it live only in conversation history.

## Known issues (deferred)

### TimescaleDB continuous aggregates (replace the hand-rolled rollup system) — IN PROGRESS
Migration `0020_continuous_aggregates` created 5 CAs (`cagg_volume_hourly` + one daily CA per
dimension: editor/imagery/locale/contributor), `WITH NO DATA` + policies, meant to eventually
replace `DailyVolume`/`DailyBreakdown` + `refresh_rollups_incremental()`/`refresh_rollups_
reconcile()` (`changesets/rollups.py`). Status: `cagg_volume_hourly` is backfilled (manual `CALL
refresh_continuous_aggregate`, run in small ranges after an initial single-huge-range attempt
appeared to stall — see that incident's own note above the corresponding commit). The 4 dimension
CAs are **not yet backfilled**. Remaining steps (see the approved plan this was built from, or
redo the design if the plan file is gone):
1. Backfill the 4 dimension CAs (same paced/monitored approach as volume).
2. Spot-check backfilled totals against current `DailyVolume`/`DailyBreakdown`.
3. Switch `TimeseriesView`/`SummaryView`/`ToplistView` to query the CAs instead (add 5 small
   unmanaged Django models) — this is also where a single-dimension `contributor`/`editor`/
   `imagery` filter on `SummaryView`/`ToplistView` stops falling back to a raw `Changeset` scan
   (`_filtered_changesets`), which is fine over a week but times out over a full year (confirmed:
   `summary/?editor=X` over a year hit gunicorn's 30s worker timeout and left an orphaned Postgres
   backend running for 2+ minutes, resistant to `pg_cancel_backend`).
4. Update `poll_sequences.py`: drop the old rollup refresh calls/flags, keep `FilterValue`'s
   incremental population (split into its own function).
5. Add auto-refresh to `import_from_dump.py` (track first `created_at` seen, `CALL refresh_
   continuous_aggregate` over the imported range at the end of the run) — otherwise a future bulk
   historical import lands outside every CA policy's `start_offset` window and stays silently
   unmaterialized until someone remembers to backfill by hand.
6. Follow-up migration: drop `DailyVolume`/`DailyBreakdown`, delete `refresh_rollups.py`, trim
   `rollups.py`.
7. Update `docs/ARCHITECTURE.md`'s rollup section.

Known scope limit, not a bug: none of the 5 CAs help a *combined* multi-dimension filter (e.g.
`editor=X&imagery=Y` together) — that still falls back to raw `Changeset` scanning, same as today.
No CA covers that shape; would need a dedicated CA per dimension *pair*, not attempted here since
it wasn't the case that actually timed out (confirmed via direct testing: raw-scanned single- and
2-dimension filtered queries are fast over realistic week-scale ranges once the missing indexes /
`work_mem` fixes from this session landed — it's specifically the *unfiltered high-cardinality*
toplist case, e.g. `dimension=contributor`, that's slow and what the CAs are actually fixing).

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

### Revisit gunicorn's gthread switch with real data
`web` moved from `--workers 2` (plain sync) to `--worker-class gthread --workers 2 --threads 8`
(`entrypoint.sh`) after directly observing the problem it's meant to fix: a single dashboard page
load fires ~10 parallel API calls (`dashboard.js`'s `Promise.all`), which saturated both sync
workers by itself and queued a trivial ~50ms query behind it for 38-158s wall-clock (queueing time,
not query cost). Not yet verified this actually resolves it in practice — revisit once there's
real request-latency/queue-time data (Datadog APM) from normal usage, not just the one-off manual
test that motivated the change.

### imagery_family stores the literal string "None" for some changesets
Genuine upstream data, not a code bug: some editing tool writes the literal OSM tag
`imagery_used=None` when no aerial imagery was used. `osm_fetcher.py`'s derivation (around line
208-219, `family = raw.split(' ')[0].split('/')[0].split('(')[0].strip()` then `imagery_family =
family or None`) faithfully parses that into the *string* `"None"` — a non-empty string, so the
`family or None` fallback doesn't catch it. Result: "None" shows up as a fake imagery provider in
the Top Imagery toplist, autocomplete, and filter dropdown. Confirmed via `FilterValue` (`field=
'imagery', value='None'`) and a sample row (`changeset_id 188725155`, `imagery_used=["None"]`).
Fix: treat known non-values (`none`/`unknown`/`n/a`/empty, case-insensitive) as real `NULL` instead
of a provider name at parse time, plus a backfill command for already-affected rows — same
one-line-derivation-plus-backfill pattern as the existing `backfill_imagery_family.py`.

### Datadog log pipeline severity remapping
Postgres `LOG:`-level lines are showing up in Datadog with `status:error`. Cosmetic/noisy, not
a functional bug. Needs a manual fix in the Datadog UI (Logs → Pipelines) — no MCP tool access
to do this one programmatically from here.

## Planned work

### Add locale as a filter param
`contributor`/`editor`/`imagery` are the only supported filter params (`_resolve_range_and_filters`,
`_FILTER_PARAMS` in `changesets/views.py`) — `locale`/`locale_family` is a `DIMENSION_FIELDS` entry
(used for `group_by`/toplist) but was never wired up as an actual filter. Concretely blocks one
thing already built: the dashboard's click-to-filter (clicking a toplist bar sets that dimension
as a filter, `dashboard.js`'s `applyFilter`) is wired for editor/imagery/contributor but
deliberately left off the Top 20 Languages chart, since there's no `locale` filter for it to set.
Same shape as the existing three — add the param, the `_filtered_changesets` clause, and (once the
continuous-aggregates work above lands) the `cagg_locale_daily`-backed single-filter fast path.

### Dashboard: new graph/section ideas
Discussed, not yet decided on a direction — revisit and pick one rather than losing the list:
- **Hashtags/campaign toplist** — researched in `docs/hashtag-campaign-data.md`. The dedicated
  `hashtags` column covers ~18.8% of changesets with genuine, diverse campaign signal (MapRoulette,
  TomTomCares, Missing Maps/MSF, regional mapathons) — enough to ship a "Top campaigns" toplist as
  a v1 with no backfill. *Not* a drop-in 5th `DIMENSION_FIELDS` entry though: `hashtags` is
  one-to-many (a changeset can carry several), unlike every existing dimension, so it needs an
  `unnest`/`jsonb_array_elements_text`-based query rather than the existing `GROUP BY <column>`
  pattern. Mining `comment` free-text for additional campaign mentions (~10% more coverage, noisier)
  is a legitimate v2, not required for v1 — see the doc for specifics.
- **StreetComplete quest breakdown** — `streetcomplete_quest_type` is its own dedicated column,
  unused anywhere in the UI, despite StreetComplete being a large share of edit volume. Same
  ready-now shape as hashtags.
- **Discussion activity** — `comments_count` exists but is never surfaced; either a "most-discussed
  changesets" list or a discussion-volume-over-time line would show where contentious/active edits
  are happening.
- **Geographic map** — every changeset carries a bbox (`min_lat`/`max_lat`/`min_lon`/`max_lon`); a
  world map showing edit density by region would likely be the most compelling addition for a
  "changeset monitor," but is a bigger, separate piece of work (needs a mapping library — Leaflet,
  most likely — and a real design pass on aggregation: heatmap tiles vs. clustered markers).
- **New vs. returning contributor trend** — needs a "first ever seen per user" concept not
  currently tracked, so more of a schema addition than a pure UI change.

### Pre-populate filter dropdowns with top values
`wireAutocomplete` (`dashboard.js`) only queries `/api/autocomplete/` once the user has typed at
least one character (`if (q.length < 1) return;`), so the contributor/editor/imagery datalists
are empty until then — no hint that typing narrows a larger list. Pre-fill each datalist with the
current top 20 (already computed for the ranking charts) on page load, so the dropdown has
immediately-useful options *and* implicitly signals "there's more, start typing" once a user
opens it and sees it's not exhaustive.

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
