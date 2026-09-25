# CLAUDE.md — OSM Monitor

Project context and architectural decisions for AI-assisted development. See `TODO.md` for the
running backlog of known issues and deferred design work — check it before starting non-trivial
work, and keep it updated as things get fixed or newly deferred.

## Project overview

Django app that ingests OSM changeset replication sequences from planet.osm.org, stores them in a
database, and serves a Chart.js dashboard plus a REST API.

Key entry points:
- `/` → `DashboardView` (Chart.js dashboard)
- `/api/changesets/` → `ChangesetQueryView` (filterable REST API, raw changeset records)
- `/api/changesets/timeseries/` → `TimeseriesView` (volume over time, optionally grouped)
- `/api/changesets/summary/` → `SummaryView` (total_changesets/total_objects/avg_objects)
- `/api/changesets/toplist/` → `ToplistView` (top 20 by dimension × metric)
- `/api/changesets/geo/` → `GeoView` (changeset density per grid cell; `resolution=coarse|fine`, the latter viewport-scoped via `bbox`)
- `/editors/` → `EditorsView` (one column per top-10 editor family for the selected range — default last year — plus an "Other editor families" column; each column has its own version drill-down toplist via `dimension=editor_version` and its own volume-over-time graph)
- `/api/docs/` → Swagger UI (drf-spectacular) for the public API, `/api/schema/` for the raw OpenAPI schema
- `/changeset_import/` → `APILandingPageView` (poller status page — live catch-up batch progress)
- `manage.py poll_sequences` → continuous background poller (this and `manage.py import_from_dump`
  are the only two ingestion paths — a one-shot HTTP-triggered import used to exist too
  (`ChangesetListView`/`ImportJobView`), removed 2026-09-19: it ran on a bare `threading.Thread`
  inside the `web` worker with no resume/recovery, wasn't linked from the dashboard, and had no
  real usage evidence)

## Architecture decisions

### Template / JS separation, async data fetch
`dashboard.html` is a thin HTML shell only (no server-rendered chart data) — it renders
instantly and reads only `{{ request.GET.xxx }}` for filter-input defaults. All Chart.js
initialisation lives in `static/js/dashboard.js`.

**Contract:** `dashboard.js` fetches from `/api/changesets/timeseries/`, `/summary/`, and
`/toplist/` (in parallel, several calls — one per chart/KPI group; see the file's `Promise.all`)
and renders the JSON responses into the KPI numbers and charts. Those endpoints are standalone,
public JSON APIs (same query params the dashboard UI uses: `start_date`, `end_date`,
`contributor`, `editor`, `imagery`, plus `group_by`/`dimension`/`metric` depending on the
endpoint) — usable directly by anyone, not just the dashboard's own JS. `DashboardView` itself is
a bare `TemplateView` with no `get_context_data` — it does no DB access.

**Why:** when the whole chart code lived inline in the template with server-rendered
`window.dashboardData`, any edit caused Claude to rewrite the entire file and risk breaking
layout or charts, and the data was only reachable by loading the HTML page. The split means:
- Chart logic changes → edit `static/js/dashboard.js` only
- HTML/layout changes → edit `dashboard.html` only
- Backend/data changes → edit `views.py` only
- The aggregated data itself is a real API endpoint other tools can call directly

### Stats split into timeseries/summary/toplist, not one bundled endpoint
`TimeseriesView`/`SummaryView`/`ToplistView` replaced a single `ChangesetStatsView` that returned
everything (daily counts, every top-N breakdown, object totals) in one bundled response. Split by
resource *shape* (analogous to Datadog's widget types), not by business concept: `timeseries`
handles anything date-bucketed (plain volume via `group_by=none`, or per-name series via
`group_by=editor|imagery|language|contributor`), `toplist` handles any ranked list
(`dimension` × `metric=count|objects`), `summary` is the handful of single-number KPIs. This
means a `contributor`×`count` or `language`×`objects` toplist — combinations the old bundled
endpoint never exposed — are just other parameter values on the same endpoint, not new code.
Shared filter-resolution/queryset logic lives in module-level helpers in `views.py`
(`_resolve_range_and_filters`, `_filtered_changesets`, `DIMENSION_FIELDS`) rather than being
duplicated per view. Trade-off: the dashboard now makes ~10 parallel requests to render instead
of 1 — acceptable since they're fetched concurrently (bounded by the slowest, not the sum) and
each is now independently small/cacheable, but a real cost avoided if the "aggregate everything"
endpoint had stayed.

### Public API + docs
All DRF views carry `@extend_schema` annotations (drf-spectacular) so `/api/docs/` stays
accurate as new endpoints/params are added — update the annotation in `views.py` alongside any
signature change, don't just rely on the docstring.

### Dimension naming: DB column vs. API param vs. UI label
Three separate names can exist for the same dimension, and they're allowed to differ — but each
layer's name must be used *consistently everywhere at that layer*, not decided ad hoc per file.
This came up concretely: the dashboard's chart title has said "Top 20 Languages" for a while, but
when the matching filter was added it was labeled "Locale" in the filter form, "locale" in the
click-to-filter hint text, and `locale` as the API query param — three different UI-facing spots
disagreeing with the one that had already shipped.

The convention, and the current mapping for every dimension:

| DB column (`Changeset` field) | API param / `DIMENSION_FIELDS` key | UI label |
|---|---|---|
| `user` | `contributor` | Contributor |
| `created_by_family` | `editor` | Editor |
| `imagery_family` | `imagery` | Imagery provider |
| `locale_family` | `language` | *(none — see below)* |
| `country_code` | `country` | Country |

`language` is the one exception to "every dimension has a UI label": it was the dashboard's 5th
dimension until 2026-09-22, when `country` replaced it there (filter, toplist chart, "over time"
chart). `language` itself was deliberately left alone at the API layer — `DIMENSION_FIELDS`, its
CAggs, `/api/docs/` — since it's a real, already-public param and removing it would be a breaking
API change per the API param rule below, not something to do as a side effect of a dashboard
change. So `language` now has an API param and no UI label at all; don't take that as license to
leave a *new* dimension's UI label out, that's specific to this one already-public, deliberately
retained case.

Rules:
- The **DB column** is internal and never exposed directly — it can stay whatever legacy/technical
  name it already has (`user`, `locale_family`, ...). Renaming it is a real migration, not a
  find-and-replace, so don't do it just to chase a display-name preference.
- The **API param** (`DIMENSION_FIELDS` key, query string name, `CAGG_MODELS` key) is the one
  stable public identifier — it's what `/api/docs/` documents and what external callers would use.
  Pick the word a human would naturally use for the concept (`contributor`, not `user`; `language`,
  not `locale`), and once it's public, treat renaming it as a breaking API change, not a quick fix.
- The **UI label** (form `<label>`, placeholder, click-to-filter hint text, chart title) should
  read naturally and may add words the API param doesn't need (e.g. "Imagery provider" for
  `imagery`), but every UI-facing string for the same dimension must agree — if the chart title
  says "Language," the filter label, placeholder, and hint text must too. When adding or changing
  a filter, grep the template and `dashboard.js` for every existing string tied to that dimension
  before picking new wording, rather than inventing a label in just the one spot being touched.

### NULL-tag volume gets its own "(none)" bucket, never silently excluded
`cagg_imagery_daily`/`cagg_locale_daily` (and any future per-dimension CAgg) group untagged rows
under a real `COALESCE(<field>, '(none)')` bucket rather than filtering them out with `WHERE
<field> IS NOT NULL`. A large campaign that never sets a given tag (e.g. imagery) still counts
toward total volume and must not vanish from that dimension's breakdown just because it left one
field blank — a `WHERE ... IS NOT NULL` CAgg silently drops that volume from its own chart while
`SummaryView`'s total keeps counting it, which reads as a data bug from the dashboard rather than a
query choice. `views.py`'s `_filtered_changesets` special-cases the `NONE_BUCKET = '(none)'`
sentinel value to filter on `<field>__isnull=True` (not `__iexact='(none)'`) so clicking that
bucket matches the real NULL rows; `dashboard.js` styles it with the same neutral gray as "Other"
rather than a random hue. `cagg_editor_daily`/`cagg_contributor_daily` don't need this —
`created_by_family`/`user` are essentially always populated in practice — but any *new*
per-dimension CAgg on a field that can legitimately be blank should use this pattern from the
start rather than adding it as a fix later.

### imagery_used stored as JSON array
`Changeset.imagery_used` is a `JSONField` holding a list of strings (e.g. `["Bing", "Mapbox"]`).
Filtering (`imagery_raw` on `ChangesetQueryView`) uses `__contains`, which on Postgres compiles to
`jsonb`'s native `@>` containment operator — a real DB-level query, not Python-side filtering.

### SequenceState
A single-row model (`SequenceState`) tracks the last ingested sequence number so that
`poll_sequences` can resume after a crash without re-importing history.

### Statement timeout: bounded by default, opt out explicitly for long jobs
The app role (`db/init/02-role-statement-timeout.sh`) defaults to `statement_timeout = '120s'` —
inverted from the old default of unbounded-unless-told-otherwise, after an orphaned backend (its
client killed) kept running an expensive query server-side with nothing left to cancel it. `web` keeps its own tighter 30s cap via connection
`OPTIONS` (`DB_STATEMENT_TIMEOUT_MS`, `docker-compose.yml`), which wins over the role default. The
`migrate` one-shot service sets `DB_STATEMENT_TIMEOUT_MS=0` the same way, the other direction,
since migration DDL (e.g. an index build over the full hypertable) can legitimately run past 120s.
**Any new one-shot management command or long-running backfill must explicitly `SET
statement_timeout = 0` on its own connection** (see `poll_sequences.py` and every
`backfill_*`/`import_from_dump`/`refresh_rollups` command for the pattern) — it is not exempted by
default, and will otherwise be silently cancelled at 120s. `poll_sequences.py` re-issues this every
loop iteration, not just once at startup, because its exception handler calls `connection.close()`
on error and the fresh reconnect after that would otherwise silently pick the role default back up.

### Geo storage: a single geohash key, not two lat/lon-grid CAggs (2026-09-19)
The geo heatmap (`GeoView`) used to be backed by two separate continuous aggregates —
`cagg_geo_daily` (coarse, 0.5° cells) and `cagg_geo_fine_daily` (fine, ~0.05° cells) — each with
its own `grid_lat`/`grid_lon` columns and its own independent 1-D index per axis. A one-month,
tight-viewport query against that design measured 2,923ms for 124 rows, almost entirely 569 random
page reads: the planner picks one axis's index, walks the whole band across the month, then
filters the other axis in memory — two 1-D indexes answering a 2-D question.

Replaced with one CAgg, `cagg_geo_hashed_daily`, keyed by `geohash` (`changesets_changeset.geohash`,
`Changeset.geohash` in the ORM — a `varchar(12)` derived by the same trigger that maintains
`centroid`, migration 0032) instead of a lat/lon pair. A coarser cell is just a shorter *prefix* of
a finer one (geohash's own nesting property), so `GeoView` serves every zoom level by truncating
one stored key (`GEOHASH_PREFIX_LENGTH` in `changesets/geo.py`) rather than choosing between two
pre-materialized grids — and because geohash sorts lexicographically the same way it nests, a
prefix-range condition (`geohash >= 'u0' AND geohash < 'u0~'`) is a normal sargable btree range
scan, so TimescaleDB's compressed-batch min/max index on `(geohash, bucket)` can skip whole
non-overlapping batches instead of visiting scattered pages. Known, accepted trade-off: geohash has
boundary discontinuities (two geographically adjacent cells near the equator/prime-meridian can
have very different prefixes) — fine for a density heatmap, not for exact-adjacency logic.

`country_code` (ISO 3166-1 alpha-2, e.g. `FR` not `France`) was added in the same migration/trigger
pass purely because both needed the same one-time full-history backfill pass and doing that twice
would have doubled the I/O cost — **country is not "free" once geohash exists**: a
geohash prefix is a regular-grid concept, country borders are irregular polygons, so it's resolved
independently via `country_boundaries` (a loaded Natural Earth admin-0 table, GiST-indexed,
point-in-polygon against `centroid`). `country` was schema + backfill only at first — no
`DIMENSION_FIELDS` entry, no CAgg pair, no dashboard wiring — until 2026-09-22, when it replaced
`language` as the dashboard's 5th dimension (see the dimension-naming table above): `cagg_country_
daily`/`hourly` (migration 0045), an `UPPER(country_code)` expression index (migration 0047, same
per-chunk `CONCURRENTLY` build as `locale_family`'s below), and 3 pair CAggs — country×editor,
country×imagery, country×contributor (migration 0048, no country×language — see PAIR_CAGGS'
comment in `views.py`). `language` itself was deliberately *not* removed from the API
(`DIMENSION_FIELDS`, its CAggs) — only from the dashboard UI — since it's a real, documented public
API param and CLAUDE.md's own dimension-naming rules treat removing one as a breaking change, not a
quick fix.

Five lessons from actually shipping this, worth remembering for the next schema change of this
shape: (1) a plain `RunSQL`-only migration that adds a real column (here: `ADD COLUMN geohash`)
without a matching `AddField` **state** operation leaves the Django model silently missing that
field — invisible until ORM code tries to `.filter()` on it, which is exactly what happened here
(`FieldError: Cannot resolve keyword 'geohash' into field`) despite the column and its index having
existed and been correctly populated in the database the whole time. (2) `CALL
refresh_continuous_aggregate(...)` over a large/dense chunk can trigger the same
parallel-worker `/dev/shm` exhaustion crash documented in `docker-compose.yml`'s `shm_size` comment
for `VACUUM ANALYZE` — `SET max_parallel_workers_per_gather = 0` around the call avoided it. This
guard only covered code that explicitly opted in (`cagg_maintenance.refresh_caggs_over_range`),
though — every CAgg's own automatic `add_continuous_aggregate_policy` background refresh runs
through TimescaleDB's internal scheduler under the same role, never through that function, so it
never got the same protection. Confirmed as a real, independent crash trigger 2026-09-22 (Postgres
crash-restarted with no backfill or migration running at all, shortly after several new CAggs —
and their background policies — had been added the same session) — fixed by making the guard a
role-level default instead (`db/init/03-role-parallel-workers.sh`, plus a live `ALTER ROLE` for the
already-existing volume), so it's inherited by every connection under that role, background
workers included.
(3) `GEOHASH_PREFIX_LENGTH['coarse']` shipped as `4` (~39km × 19.5km) on the assumption that it was
"close enough" to the old design's 0.5° cells — it wasn't: geohash halving doesn't land near 0.5°
at any integer precision, and 4 actually produced **~4x more cells globally** than the old grid
(25K+ for a default unfiltered load), enough `L.rectangle` draws to make the map appear to hang in
a real browser. Corrected to `3` (~156km × 156km, ~4.4K cells for the same load) after actually
counting distinct cells for a candidate precision before shipping it, not just eyeballing the
degree size. (4) The unfiltered/CAgg-backed path (`GeoView._from_rollups`) initially had **no
SQL-level bbox filter at all** for `resolution=fine` — the CAgg stores only `geohash`, not a real
geometry column to bbox-overlap against (unlike the raw-table fallback, which uses `centroid`), so
a "just crop in Python after decoding" design silently became "fetch every geohash on Earth for
the date range, then crop" once a real viewport was involved (measured: ~870K rows for a
1.5-month range). Fixed by pre-filtering with the longest common geohash prefix covering the
bbox's SW/NE corners (`geohash_bbox_prefix()` in `changesets/geo.py`) before the exact crop — a
~10x speedup on real viewports. The lesson: "decode a few hundred result rows in Python" is only
true once the *input* to that decode is already spatially bounded — check what actually reaches
the query, not just what the final response looks like. (5) `resolution=fine` originally used one
fixed precision (`GEOHASH_PRECISION=6`, ~1.2km cells) for every fine-mode request regardless of
viewport size — but the dashboard's fine/coarse switch fires at one fixed *zoom level*
(`GEO_FINE_ZOOM_THRESHOLD`), and that zoom level's viewport can span a small country or a single
neighborhood depending on screen size and location, so a fixed cell size is wrong everywhere except
the one viewport width it happened to be eyeballed against. At the zoom level this dashboard
switches into fine mode at, a 1.2km cell measured under a screen pixel wide across the actual
viewport — present in the API response, invisible on the map, until zooming in much further than
the threshold that was supposed to already show detail ("looks blank, then little squares appear"
was the exact symptom reported). Fixed with `geohash_precision_for_bbox()`: pick the coarsest
precision that still gives a target cell count (~15) across the *more constrained* bbox dimension,
so cell density stays visually legible at every zoom level fine mode can trigger at, not just one.
The lesson generalizes: any "fine-grained" query resolution that's reachable from more than one
concrete request shape (here: viewport size varies by screen/location, not just by the user's
explicit resolution choice) needs to adapt to the request, not assume the one shape it was tuned
against. Full implementation history (measurements,
crash investigation, the old CAgg's own incidental data gap found during verification) is not kept
as a live doc — see git history around 2026-09-18/19 if it's ever needed again; the schema and code
themselves are the current source of truth.

**Sixth lesson, client-side rather than schema**: the map's fine-mode fetch originally ran on every
`moveend`/`zoomend` (just debounced 400ms) — correct in the sense that it always showed the right
data, but normal map browsing alone (not misuse) generated a real DB query for nearly every pan or
zoom, far more load than the map's actual information needs justified. Fixed in `static/js/dashboard.js`'s
`scheduleFineFetch`: fetch a padded area beyond the visible viewport (`GEO_FINE_PREFETCH_PAD`) and
skip the request entirely whenever the current viewport is still inside the last-fetched padded
area, plus cancel a still-in-flight fetch when a newer one supersedes it (`AbortController`) so
fast browsing can't pile up concurrent queries whose results just get thrown away anyway. Because
`geohash_precision_for_bbox()` (lesson 5 above) derives cell size from whatever bbox it's given,
padding the fetched area would have also made cells look coarser than intended — compensated by
doubling `FINE_TARGET_CELLS_ACROSS` to account for the known 2x-span padding, not by adding a
second bbox parameter. If `GEO_FINE_PREFETCH_PAD` ever changes, that constant needs to move with
it (both have a comment cross-referencing the other).

**That fix immediately broke something else**, caught the same day: "still geographically inside
the last-fetched padded area" is not the same question as "is the last-fetched data fine enough
for here" once cell size adapts to viewport (lesson 5) — zooming in further almost always stays
inside the *previous*, larger padded fetch, so the skip-the-request condition kept firing forever
past the first fine-mode fetch, and the map got visibly stuck at whatever precision that first
fetch happened to pick, no matter how much further the user zoomed in ("only 2 levels of zoom").
Fixed by also tracking the zoom level a cached fetch was made at (`fineFetchedZoom`) and only
reusing the cache when the current zoom is no deeper than that — zooming in past it always forces
a real fetch, even when still geographically contained. The lesson: a cache-reuse condition built
around one axis of "is this still valid" (here: geographic coverage) silently assumed a second axis
(resolution) couldn't independently go stale — true for a fixed cell size, false the moment cell
size became adaptive. Check every axis a cached response actually depends on, not just the one the
cache key was originally built around.

### TimescaleDB hypertable
`changesets_changeset` is a TimescaleDB hypertable (monthly chunks on `created_at`) — see
`docs/ARCHITECTURE.md`'s "Why a hypertable" section for the full reasoning and the PK/unique-
constraint trade-off it required (`id` is no longer DB-enforced-unique; real duplicate protection
is the composite `UNIQUE(changeset_id, created_at)` added in migration
`0018_timescale_hypertable`). Django ORM code is otherwise unaffected.

### Old-dated rows in the replication stream are normal — don't "fix" them
**Read this before concluding anything about data coverage, backfill health, or the continuous
aggregates' 7-day refresh window.** It reverses two conclusions that look obvious from the data
alone and have already been reached (wrongly) once.

The replication stream at planet.osm.org publishes changesets whose *state changed*, not
changesets that were just created. Two things put a changeset in a sequence file:

1. It was created/closed recently — the overwhelming majority.
2. **Someone posted a comment on it.** Changeset discussions have no upper age limit: commenting
   requires the changeset to be *closed*, but a 2014 changeset can be commented on today and will
   reappear in today's sequence file carrying its original 2014 `created_at`.

So a handful of very old rows sitting in otherwise-empty ancient chunks is the **expected,
correct** result of live polling. It is *not* evidence of a stalled backfill, a clock bug, or a
bad import. Confirmed empirically (2026-09-17): every one of the 503 pre-2025 rows has
`comments_count > 0`, all are closed, and all carry high `id` values — i.e. they were inserted
recently by the live poller, not by the backfill. In the most recent ~74,000 inserted rows, 73,955
were 0-7 days old and 22 were over a year old (all 22 with comments); **nothing landed in
between**.

Note the mechanism is the comment, not a "comments close after 7 days" rule — there is no such
rule in OSM, and the 2014-2024 rows above disprove it. Don't write that assumption back in.

**Why `start_offset = 7 days` on all 12 CAgg refresh policies is nevertheless correct**, and must
not be widened to "cover" those old rows:

- The only aggregate-relevant field that can change after insert is `changes_count`, and that can
  only grow while the changeset is still *open* — bounded by OSM's 24h max open time / 1h idle
  timeout. 7 days is a generous margin over 24h, not an arbitrary guess.
- A comment-only reappearance of a changeset we already have writes nothing at all:
  `import_changeset_batch` (`osm_fetcher.py`) skips it unless `changes_count` actually grew.
- Widening the window would force a rescan of months of already-final buckets every 30-60 minutes,
  to correct nothing.

**The one real gap this leaves**, worth knowing but not worth widening the window for: an old
changeset we have *never seen before*, arriving because of a comment, is inserted into an ancient
chunk and will never be materialized into any CAgg — a tiny fraction of rows. More generally, any
date range the CAggs haven't materialized returns `0` rather than an error — see `TODO.md`'s
"Empty stats for unmaterialized ranges" entry, and treat that as a *reporting* problem (say "no
coverage"), not a refresh-policy problem.

**Where the 7-day window genuinely is not enough:** a deliberate backward/bulk import
(`import_from_dump`, or `poll_sequences`' backfill). Those write large volumes far outside the
window and need an explicit refresh over the imported range: `import_from_dump` does it itself at
the end, and `refresh_caggs <start> <end>` covers runs with `--skip-cagg-refresh` (parallel
`--byte-range` workers) or any other bulk write.

### Design for full history
The target dataset is full 2005-present OSM changeset history (~190M+ rows, growing), not a recent
subset — a deployment may hold less (e.g. only the poller's default 365-day backfill), but code
must not assume it. When adding a query, index, or background job, sanity-check it against the
full-history row count and chunk count rather than whatever dataset it was developed against.
Two concrete examples already hit by this project: the old `DailyVolume`/
`DailyBreakdown` rollup refresh loop was watermarked on `id` (not the hypertable's partitioning
column), so it got no chunk exclusion and its cost grew with total chunk count regardless of how
much data had actually changed — measured at ~15% of total DB time before being
replaced by the CAgg design (see "Aggregates" in `docs/ARCHITECTURE.md`); `TimeseriesView`'s
bucket-width picker (`_pick_interval`, migration `0023`) only offers hour/day grains — daily is
~400 points over a year but thousands over multi-year ranges, so a weekly/monthly tier is the
natural next step once multi-year ranges are common.
Prefer incremental/watermark-based designs over periodic full-table rebuilds, and watermark on the
hypertable's partitioning column (`created_at`) specifically, not a surrogate key like `id`.

## File map

| Path | Role |
|---|---|
| `changesets/models.py` | `Changeset` (hypertable) + rollup/state/job models |
| `changesets/views.py` | All views (dashboard, API, import landing) |
| `changesets/serializers.py` | DRF serializer for `Changeset` |
| `changesets/urls.py` | API URL patterns (`/api/…`) |
| `osm_changeset_api/urls.py` | Root URL conf (mounts API + dashboard) |
| `changesets/osm_fetcher.py` | Fetches & parses OSM replication XML |
| `changesets/rollups.py` | Precomputed daily aggregates behind the unfiltered dashboard view |
| `changesets/geo.py` | Shared grid-cell/bbox-quality SQL for the geo heatmap (`cagg_geo_daily`/`cagg_geo_fine_daily` + `GeoView`) |
| `changesets/management/commands/poll_sequences.py` | Long-running poller |
| `changesets/management/commands/import_from_dump.py` | Bulk planet-dump importer |
| `changesets/templates/changesets/dashboard.html` | Dashboard HTML shell only |
| `changesets/templates/changesets/editors.html` | Editors-page HTML shell only |
| `static/js/common.js` | Shared chart factories / apiUrl/fetchJson/loadWidget / geo map / autocomplete helpers, used by both dashboard.js and editors.js |
| `static/js/dashboard.js` | Dashboard-page-specific widget wiring |
| `static/js/editors.js` | Editors-page-specific widget wiring |
| `static/output.css` | Compiled Tailwind CSS |
| `db/init/` | One-time Postgres setup (extensions, Datadog schema) for a fresh DB |
| `docs/ARCHITECTURE.md` | Deep dive: data flow, why TimescaleDB, observability, deployment |
| `docs/DEPLOYMENT.md` | Running the Compose stack, optional Datadog overlay (`docker-compose.datadog.yml`), full-history import |

## Development notes

- Static files in `DEBUG` mode are served by Django via `django.conf.urls.static`.
- Initial sequence on first poller run: pass `--start <seq>` or set `INITIAL_SEQUENCE` env var.
- `TimeseriesView`/`SummaryView`/`ToplistView` (aggregated stats) default to the last 7 days when
  no date params are given; `ChangesetQueryView` (raw record list) defaults to the last 24 hours
  — it has no unfiltered "everything" mode, see its docstring in `views.py`.

## Documentation conventions

### TODO.md stays an index — details go in `docs/todo/`
`TODO.md` grew past the point of being skimmable — each entry had accreted its full investigation
history (evidence, numbers, false starts) inline. As of 2026-09-18 it holds **one line per item
only**: enough to know what it is and whether it's relevant, with a link to
`docs/todo/<slug>.md` for the reasoning, evidence, and remaining steps. Same split this project
already uses elsewhere — `dashboard.js`/`views.py`/`dashboard.html` stay separate for the same
reason (see "Template / JS separation" earlier in this file), and this AI's own persistent memory
system uses the identical index-plus-detail-file pattern (a short `MEMORY.md` index, one file per
topic).

Rules when touching either file:
- A new deferred item gets a one-line entry in `TODO.md` **and** a `docs/todo/<slug>.md` file —
  never just a paragraph dropped into `TODO.md` directly, even for something that feels small
  enough to skip the split. If it's not worth a sentence in the index, it's not worth tracking.
- When an item is fully resolved, delete its `TODO.md` line. Move genuinely durable
  "why" reasoning into the relevant architecture-decision section of *this* file (as its own
  dated note if it doesn't fit an existing section); otherwise let the detail file be superseded
  by git history rather than keeping it as a live reference.
- Never let a `docs/todo/*.md` file exist with no line in `TODO.md` pointing to it — that's an
  orphaned file nobody will find. If a topic changes shape (split, merged, superseded), update
  both the link text and the file's own contents together, in the same change.
