# Dashboard: new graph/section ideas

Discussed, not yet decided on a direction for the ones still open — revisit and pick one rather
than losing the list.

## Still open

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
- **New vs. returning contributor trend** — needs a "first ever seen per user" concept not
  currently tracked, so more of a schema addition than a pure UI change.

## Done

**Per-country breakdown.** `country` replaced `language` as the dashboard's 5th dimension
(2026-09-22) — filter, toplist chart, and "over time" chart, matching the other three dimensions'
full treatment. `DIMENSION_FIELDS`/`CAGG_MODELS` entries, `cagg_country_daily`/`hourly`
(migration 0045) and 3 pair CAggs (migration 0048) — see `docs/todo/continuous-aggregates-
migration.md`'s 2026-09-22 entry and `CLAUDE.md`'s dimension-naming table.

**Geographic map.** `GeoView` (`/api/changesets/geo/`, `changesets/views.py`) + `cagg_geo_hashed_daily`
(migration `0033_cagg_geo_hashed_daily`) + a Leaflet grid map on the dashboard (`dashboard.js`'s
`renderGeoMap` — one `L.rectangle` per cell, colored by a log-scale sequential blue ramp with a
legend, not a Leaflet.heat blurred/interpolated blob layer: rectangles show the true cell
boundary/color rather than an approximated surface between sparse points, which was the actual
cause of "hard to see detail" — the resolution itself was the separate, bigger lever). As of
2026-09-19, cell shape is geohash-derived (not the fixed-degree grid described below) — see
`CLAUDE.md`'s "Geo storage: a single geohash key" section.

Grid-cell (0.5°) and bbox-quality-gate expressions live in `changesets/geo.py`, shared between the
CAgg's defining query and `GeoView`'s raw-fallback path so they can't drift. Bbox-quality
threshold (200km bbox diagonal, excluding changesets whose bbox is too big to trust a centroid
from) came from a live sample (6h, n=3000): p50=0.2km, p99=49km, max=477km — should be
re-validated on a bigger/multi-day sample before being treated as final; retuning means
dropping+recreating the CAgg (can't `ALTER` a CA's defining query) plus redoing the backfill.
Migrated, backfilled (all 138 chunks, 2005-2026), and verified: 0 day-level gaps vs.
`cagg_volume_daily`, ~1.12% of changesets excluded (NULL bbox or over the 200km threshold) — in
line with the empirical estimate.

**Zoom-dependent resolution + metric toggle, also done.** A second CAgg, `cagg_geo_fine_daily`
(migration `0027_cagg_geo_fine_daily`, `FINE_GRID_SIZE_DEGREES = 0.05°` in `changesets/geo.py`),
backed `GeoView`'s `resolution=fine` mode — same bbox-quality gate, same "CAgg when unfiltered, raw
fallback when filtered" split as the coarse path, but always scoped to a `bbox` viewport param
(required for `fine`) since shipping every 0.05° cell on Earth would be excessive payload for one
zoomed-in view. Chosen over computing fine cells live from the raw table per-request (an earlier
design) because this host's DB is slow enough under load that paying the cost in storage (a
second full-history CAgg) beats paying it in query latency — disk space was the cheaper resource
here. Backfilled (137 more `refresh_continuous_aggregate` calls; the 138th chunk was already
covered by an earlier timing test), verified the same way as the coarse CAgg: 0 day-level gaps,
~1.16% excluded. `dashboard.js`'s `renderGeoMap` switches between a `coarseLayer` and `fineLayer`
`L.layerGroup` at `GEO_FINE_ZOOM_THRESHOLD` (zoom 7), fetching fine data for the current viewport
(debounced 400ms) only once zoomed in past that — the coarse layer's initial fetch is cached and
reused, never refetched. A segmented control (`GEO_METRICS`, top-right on the map) switches which
value (`count` vs. `objects`) drives cell color/legend — pure client-side restyle of whichever
cells are already fetched, no refetch, since both values are in every cell.

Noted while backfilling `cagg_geo_fine_daily`: dense recent months take ~2-3 minutes each at this
grid size (vs. ~20-60s for the coarse 0.5° grid) — expect a similar multi-batch, monitored
backfill for any future geo materialization at this size or finer.
