# Geo storage: a single geohash key, not two lat/lon-grid CAggs (2026-09-19)

> **2026-10-05:** TimescaleDB and Postgres were removed ([timescale-removal.md](timescale-removal.md)); what this says about them (CAggs, the hypertable, Postgres queries) is history, the rest still holds.

The geo heatmap (`GeoView`) used to be backed by two separate continuous aggregates —
`cagg_geo_daily` (coarse, 0.5° cells) and `cagg_geo_fine_daily` (fine, ~0.05° cells) — each with
its own `grid_lat`/`grid_lon` columns and its own independent 1-D index per axis. A one-month,
tight-viewport query against that design measured 2,923ms for 124 rows, almost entirely 569 random
page reads: the planner picks one axis's index, walks the whole band across the month, then
filters the other axis in memory — two 1-D indexes answering a 2-D question.

Replaced with one CAgg, `cagg_geo_hashed_daily`, keyed by `geohash` (`changesets_changeset.geohash`,
`Changeset.geohash` in the ORM — a `varchar(12)` added in migration 0032, computed at ingest
since migration 0057, see [location-at-ingest.md](location-at-ingest.md)) instead of a lat/lon pair. A coarser cell is just a shorter *prefix* of
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
independently, by point-in-polygon against Natural Earth admin-0 borders
(`changesets/data/country_boundaries.geojson`), at ingest since migration 0057. `country` was schema + backfill only at first — no
`DIMENSION_FIELDS` entry, no CAgg pair, no dashboard wiring — until 2026-09-22, when it replaced
`language` as the dashboard's 5th dimension (see [dimension-naming.md](dimension-naming.md)): `cagg_country_
daily`/`hourly` (migration 0045), an `UPPER(country_code)` expression index (migration 0047, same
per-chunk `CONCURRENTLY` build as `locale_family`'s), and 3 pair CAggs — country×editor,
country×imagery, country×contributor (migration 0048, no country×language — see PAIR_CAGGS'
comment in `changesets/analytics/timescale/caggs.py`). `language` itself was deliberately *not* removed from the API
(`DIMENSION_FIELDS`, its CAggs) — only from the dashboard UI — since it's a real, documented public
API param and the dimension-naming rules ([dimension-naming.md](dimension-naming.md)) treat removing one as a breaking change, not a
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
