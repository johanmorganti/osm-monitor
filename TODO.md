# TODO — known issues & deferred design work

Running backlog of things found during development but not yet fixed, plus the
reasoning behind them. Keep this current: when something here gets fixed, move
it out (delete the entry, or fold the resolution into CLAUDE.md's architecture
decisions if it's worth remembering *why*); when something new is deferred,
add it here rather than letting it live only in conversation history.

## Known issues (deferred)

### TimescaleDB continuous aggregates (replace the hand-rolled rollup system) — MOSTLY DONE
Migration `0020_continuous_aggregates` created 5 CAs (`cagg_volume_hourly` + one daily CA per
dimension: editor/imagery/language/contributor), meant to eventually replace `DailyVolume`/
`DailyBreakdown` + `refresh_rollups_incremental()`/`refresh_rollups_reconcile()`
(`changesets/rollups.py`). Status: all 5 CAs are backfilled (manual `CALL
refresh_continuous_aggregate`, one chunk-month at a time — 56 calls total — after an initial
single-huge-range attempt on `cagg_volume_hourly` appeared to stall), spot-checked against raw
`changesets_changeset` as ground truth (the *old* rollup tables turned out to be the stale ones,
off by ~15%, not the new CAs — this session's poller downtime had let them drift), and `views.py`
now queries them: `TimeseriesView`/`SummaryView`/`ToplistView`'s unfiltered paths, plus
`SummaryView`'s and (as of the group_by=None case) `TimeseriesView`'s single-dimension-filtered
fast paths. Confirmed fixed: `summary/?editor=X` over a full year (was: 30s gunicorn timeout +
orphaned Postgres backend) now 66ms; unfiltered `toplist/?dimension=contributor&metric=objects`
over 3 months (was: 30s+ timeout) now ~1.9s; `timeseries/?imagery=X` (no group_by) over 3 months
(was: 30s timeout) now ~240ms.

Remaining steps:
4. Update `poll_sequences.py`: drop the old rollup refresh calls/flags, keep `FilterValue`'s
   incremental population (split into its own function).
5. Add auto-refresh to `import_from_dump.py` (track first `created_at` seen, `CALL refresh_
   continuous_aggregate` over the imported range at the end of the run) — otherwise a future bulk
   historical import lands outside every CA policy's `start_offset` window and stays silently
   unmaterialized until someone remembers to backfill by hand.
6. Follow-up migration: drop `DailyVolume`/`DailyBreakdown`, delete `refresh_rollups.py`, trim
   `rollups.py`.
7. Update `docs/ARCHITECTURE.md`'s rollup section.

**Real, confirmed remaining gap (corrects this entry's earlier "known scope limit" note, which
was wrong about week-scale-only being the risk)**: any *cross-dimension* filtered query — a
`contributor`/`editor`/`language` toplist filtered by `imagery` (or vice versa), or `TimeseriesView`
with both a `group_by` and a filter set — has no matching CA (each CA only tracks its own single
dimension) and falls back to raw `Changeset` scanning. Confirmed via direct test: `toplist/
?dimension={contributor,editor,imagery,language}&imagery=Mapbox` over a 3-month range all hit the
30s statement timeout and got cancelled, for every dimension tried. Root cause, confirmed via
`timescaledb_information.compression_settings`: the hypertable's compression is `segmentby
created_by_family` (editor) only (see "Compression backlog" below) — a query that filters or
groups by anything *other* than editor can't exclude compressed segments and ends up decompressing
everything in range. This is *not* fixable by adding more CAs (a CA per dimension *pair* would be
needed, and doesn't scale — see CLAUDE.md's "design for full history" principle); the real fix is
either widening `compress_segmentby` (cost: lower compression ratio, and a backlog-recompression
pass — see below) or accepting that cross-dimension-filtered toplists stay slow/degrade gracefully
(e.g. UI disables or caps the range for that combination). Needs a decision before doing more work
here.

### Per-dimension CAs silently drop NULL-tag volume (large campaigns can vanish from breakdowns)
Confirmed via investigation (2026-09-14) into a real symptom: the main "Changesets Over Time"
chart (`cagg_volume_hourly`, no filter) showed a clear volume spike on 2026-08-30, but none of the
"Top Imagery/Language Over Time" breakdown charts showed any corresponding movement. Root cause:
`cagg_imagery_daily`/`cagg_locale_daily`/`cagg_editor_daily` are each defined with a
`WHERE <field> IS NOT NULL` clause (see migration `0020_continuous_aggregates`), while
`cagg_volume_hourly` has no such filter. The spike was a ~9,000-changeset MapRoulette campaign
(challenge 56565, bulk-updating Italian municipality population from ANPR open data, mostly two
users) — a scripted one-object-per-task edit pattern that never sets `imagery_used`/`locale`, so
100% of it has `imagery_family`/`locale_family` NULL. Those changesets count fully toward total
volume but are structurally invisible to the imagery/language breakdowns no matter how large the
campaign — not a bug in the CA backfill (editor breakdown *does* show it correctly, since
`created_by_family` is populated; verified the imagery/locale CAs' non-NULL totals for that day
match raw data almost exactly, i.e. nothing is actually missing/stale, the NULL rows are just
excluded by design). Any future large NULL-tag campaign (bulk imports, other MapRoulette-style
bot edits) will reproduce this same "spike with no explanation" experience.
Fix, if these breakdowns are meant to explain "why did total volume move": drop the
`IS NOT NULL` filter on `cagg_imagery_daily`/`cagg_locale_daily` and add a `COALESCE(name, '(none)')`
bucket instead, so NULL-tag volume shows up as its own explicit series rather than silently
vanishing. Related to the "Dashboard: new graph/section ideas" hashtags/campaign toplist entry
below — a hashtag-based campaign breakdown would make cases like this MapRoulette campaign
directly attributable by name instead of just "(none)". Not urgent (understood gap, not active
data corruption), but low-effort once picked up.

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
redo it later. **Confirmed concrete cost of the current editor-only choice**: see the continuous-
aggregates entry above — any toplist/timeseries query filtered or grouped by `imagery`/`language`/
`contributor` (i.e. every dimension except editor) can't exclude compressed segments and times out
over multi-month ranges once real data hits those chunks.

### Revisit gunicorn's gthread switch with real data
`web` moved from `--workers 2` (plain sync) to `--worker-class gthread --workers 2 --threads 8`
(`entrypoint.sh`) after directly observing the problem it's meant to fix: a single dashboard page
load fires ~10 parallel API calls (`dashboard.js`'s `Promise.all`), which saturated both sync
workers by itself and queued a trivial ~50ms query behind it for 38-158s wall-clock (queueing time,
not query cost). Not yet verified this actually resolves it in practice — revisit once there's
real request-latency/queue-time data (Datadog APM) from normal usage, not just the one-off manual
test that motivated the change.

**Deliberately parked, no action needed until there's real traffic** (confirmed with the user
2026-09-11) — there isn't any yet, so there's no real data to check against and a synthetic load
test was declined in favor of waiting. If dashboard load ever feels slow or times out again, this
is the first thing to check: in Datadog APM for the `osm-monitor` (web) service, look at request
latency/duration for `/api/changesets/*` endpoints split from queueing/wait time if that's exposed
per-span, and gunicorn's own worker/thread saturation (busy vs idle threads) if visible. The
concrete symptom that would mean gthread *isn't* enough: many concurrent requests (e.g. a page
load's ~10 parallel calls, or several users at once) showing high wall-clock time despite the
underlying query itself being fast in the DB — that's the queueing signature this change was
meant to fix, same as the 38-158s-for-a-50ms-query incident that motivated it. If that shows up
again with gthread already in place, the next lever is more threads/workers (mind Postgres's
`max_connections=100` headroom) rather than assuming the query layer regressed.

### Datadog log pipeline severity remapping
Postgres `LOG:`-level lines are showing up in Datadog with `status:error`. Cosmetic/noisy, not
a functional bug. Needs a manual fix in the Datadog UI (Logs → Pipelines) — no MCP tool access
to do this one programmatically from here. User is fixing directly, 2026-09-14.

### Filter dropdown pre-population reported not working
A prior pass (2026-09-11) wired the contributor/editor/imagery datalists to pre-fill from each
ranking chart's already-fetched toplist response (`setDatalistOptions` in `dashboard.js`, called
from the `loadWidget` success callbacks) instead of staying empty until the user types. Verified
only via served-JS/HTML inspection at the time (no browser tool available) — user reports it's
not actually working in a real browser. Not yet root-caused; set aside until revisited. Worth
checking when picked back up: whether `<datalist>` options are actually reaching the DOM (browser
devtools/inspect, not just curl'd HTML/JS), and whether native datalist UI (no visible dropdown
arrow, appears only once the input is focused/clicked) is being mistaken for "empty" when it's
actually populated but just not visually obvious.

## Planned work

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
