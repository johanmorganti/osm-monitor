"""Shared SQL fragments for the changeset geo/heatmap feature (GeoView in
views.py; migrations 0025_cagg_geo_daily and 0027_cagg_geo_fine_daily) —
kept in one place so the two continuous aggregates' defining queries and
GeoView's raw-fallback queryset can never silently drift on grid size or
bbox-quality threshold; they live in different files and a normal diff
wouldn't catch that drift.

A changeset's bbox (min/max lat/lon) is the envelope of every object it
touched, so a single stray far-away object can balloon it and make the
centroid meaningless. BBOX_DIAG_THRESHOLD_KM excludes those from the map
(they still count everywhere else — SummaryView, TimeseriesView,
ToplistView) rather than plotting them somewhere misleading. Chosen from a
live sample (6 hours, n=3000) of bbox diagonal size: p50=0.2km, p90=4.3km,
p99=49km, p999=235km, max=477km — 200km sits in the empirically-justified
100-300km range, but should be re-validated on a bigger sample before being
treated as final (see the geo heatmap plan). Retuning it means dropping and
recreating cagg_geo_daily (continuous aggregates can't have their defining
query ALTERed in place) plus redoing the backfill, so it's worth getting
close to right rather than iterating in place.
"""

GRID_SIZE_DEGREES = 0.5
BBOX_DIAG_THRESHOLD_KM = 200

# GeoView's "fine" resolution — a second continuous aggregate
# (cagg_geo_fine_daily, migration 0027) at this grid size, viewport-scoped
# at query time (min/max_lat/lon) since even pre-aggregated, shipping every
# fine cell on Earth would be a lot of payload/render for a single zoomed-in
# view. Not computed live from the raw table: an earlier design did that,
# but a second CAgg — paid for in storage, not query time — was the better
# trade.
# ~0.05° ≈ 5.5km — neighborhood scale, a reasonable target for "zoomed in
# past the coarse 0.5° grid."
FINE_GRID_SIZE_DEGREES = 0.05

# Equirectangular approximation, not haversine — deliberately: this is a
# coarse quality gate on bbox size, not a distance shown to users, and it's
# accurate to a few percent in the regime this threshold actually operates
# in (a handful of hundred km at most).
BBOX_DIAG_KM_SQL = (
    "sqrt("
    "power((max_lat - min_lat) * 111.0, 2) + "
    "power((max_lon - min_lon) * 111.0 * cos(radians((min_lat + max_lat) / 2.0)), 2)"
    ")"
)

# Round-to-nearest (not truncation) so a cell is centered on its own
# coordinate rather than snapping toward zero. Parameterized by grid size —
# GeoView's "fine" (viewport-scoped, always-raw) resolution uses a smaller
# size than the CAgg-backed "coarse" default; see grid_lat_lon_sql().
def _grid_lat_sql(grid_size):
    return f"round(((min_lat + max_lat) / 2.0) / {grid_size}) * {grid_size}"


def _grid_lon_sql(grid_size):
    return f"round(((min_lon + max_lon) / 2.0) / {grid_size}) * {grid_size}"


def grid_lat_lon_sql(grid_size):
    """(grid_lat_sql, grid_lon_sql) gated by the bbox-quality threshold —
    NULL when the bbox is too big to trust, same rule regardless of grid
    size. Callers exclude NULL cells from the map rather than plotting a
    misleading centroid."""
    lat = f"CASE WHEN {BBOX_DIAG_KM_SQL} <= {BBOX_DIAG_THRESHOLD_KM} THEN {_grid_lat_sql(grid_size)} ELSE NULL END"
    lon = f"CASE WHEN {BBOX_DIAG_KM_SQL} <= {BBOX_DIAG_THRESHOLD_KM} THEN {_grid_lon_sql(grid_size)} ELSE NULL END"
    return lat, lon


# The two CAggs' own defining queries (migrations 0025, 0027) are built at
# these exact grid sizes — kept as module-level constants since the
# migrations already reference them directly, and re-deriving them via
# grid_lat_lon_sql(...) at import time would be equivalent but less
# obviously tied to what's actually materialized in the DB.
GRID_LAT_GATED_SQL, GRID_LON_GATED_SQL = grid_lat_lon_sql(GRID_SIZE_DEGREES)
FINE_GRID_LAT_GATED_SQL, FINE_GRID_LON_GATED_SQL = grid_lat_lon_sql(FINE_GRID_SIZE_DEGREES)


# ── PostGIS-based fragments (migration 0029) ────────────────────────────────
# `changesets_changeset.centroid` is a real geometry(Point, 4326), GiST-
# indexed, maintained by a database trigger (not application code — see
# that migration) using the same BBOX_DIAG_THRESHOLD_KM gate as above, but
# via real geodesic ST_Distance rather than the equirectangular
# approximation — centroid is already NULL for untrustworthy bboxes, so
# callers just need `grid_lat IS NOT NULL`, no CASE/threshold repeated here.
#
# Only used by GeoView's raw (filtered) fallback path — the two CAggs above
# still use the pre-PostGIS math verbatim (rebuilding them would mean
# redoing their multi-hour backfill for output that wouldn't even change
# value). The fallback path is exactly where this earns its keep: the old
# grid_lat_lon_sql() expression couldn't be indexed at all, so a filtered
# request combined with a tight viewport did a full per-row recompute
# across the whole date range regardless of how small the viewport was
# (measured: 7.3s for one week + one editor filter). `centroid` being a
# real indexed column fixes both sides of that — cheap ST_X/ST_Y instead of
# recomputing the CASE/trig expression, and the `&&` bbox-overlap operator
# (see viewport_overlap_sql) can actually use the GiST index to prune rows
# before they're scanned, not just after.
def centroid_grid_lat_sql(grid_size):
    return f"round(ST_Y(centroid) / {grid_size}) * {grid_size}"


def centroid_grid_lon_sql(grid_size):
    return f"round(ST_X(centroid) / {grid_size}) * {grid_size}"


def viewport_overlap_sql():
    """Parameterized (min_lon, min_lat, max_lon, max_lat) GiST-indexed bbox
    overlap check — pass as the `params` list alongside this SQL string to
    RawSQL(), in that exact order (matches ST_MakeEnvelope's own arg
    order)."""
    return "(centroid && ST_MakeEnvelope(%s, %s, %s, %s, 4326))"


# ── Geohash-based grid (migration 0032) ─────────────────────────────────────
# Replaces GRID_SIZE_DEGREES/FINE_GRID_SIZE_DEGREES's two-CAgg, two-column
# (grid_lat/grid_lon) design — see CLAUDE.md's "Geo storage: a single
# geohash key" section for the full reasoning (measured 2.9s/569-random-seek
# cost of that design,
# root-caused to two independent 1-D indexes for a 2-D question). A single
# ordered text key collapses coarse+fine into one CAgg: a coarser cell is
# just a shorter *prefix* of a finer one, so one stored column serves every
# zoom level by truncation instead of two near-duplicate materializations.
#
# The GRID_SIZE_DEGREES/FINE_GRID_SIZE_DEGREES constants and their *_SQL
# fragments above are kept, unchanged, only because migrations 0025 and 0027
# still import them and a migration must stay replayable on a fresh
# database — they are not used by any current view code.
#
# Precision 6 (~1.2km x 0.61km cells) — finer than the old fine grid's 0.05°
# (~5.5km), for headroom to zoom in further later without another schema
# change. GeoView resolves a requested resolution/zoom to a prefix length in
# [1, GEOHASH_PRECISION] at query time (see views.py) rather than storing
# multiple precisions.
GEOHASH_PRECISION = 6

# geohash's own base32 alphabet omits a, i, l, o — using '~' (sorts after
# every one of them, and after every digit) turns `geohash LIKE 'prefix%'`
# into a sargable `geohash >= 'prefix' AND geohash < 'prefix~'` range scan
# that a plain btree index can serve directly, no LIKE/pattern index needed.
GEOHASH_PREFIX_UPPER_BOUND_CHAR = '~'


def geohash_prefix_range_sql():
    """Parameterized (prefix, prefix) sargable range-scan check for
    `geohash` starting with `prefix` — pass [prefix, prefix + '~'] as the
    `params` list alongside this SQL string to RawSQL()."""
    return "(geohash >= %s AND geohash < %s)"


# GeoView's resolution=coarse prefix length (global, unfiltered-by-viewport
# — there's no bbox to adapt to). 3 (~156km x 156km), not 4 (~39km x
# 19.5km) as originally picked: measured 2026-09-19 that prefix 4 produces
# ~4x more cells globally than the old fixed 0.5° grid it replaced (geohash
# halving doesn't land on a size close to 0.5° at any integer precision — 3
# undershoots, 4 overshoots) — 25K+ cells for a default 7-day unfiltered
# load, enough `L.rectangle` draws to make the map appear to hang/not
# render in a real browser. 3 measured at ~4.4K cells for the same range,
# close to the old design's actual cell count. Re-check against real usage
# if this still feels too coarse or too fine once there's real traffic to
# look at (same "don't treat the first number as final" caution as
# BBOX_DIAG_THRESHOLD_KM).
#
# resolution=fine has no single fixed prefix length — see
# geohash_precision_for_bbox() below for why a fixed one (originally
# GEOHASH_PRECISION=6 unconditionally) doesn't work.
GEOHASH_PREFIX_LENGTH = {'coarse': 3}

# Cells per viewport dimension that geohash_precision_for_bbox() targets —
# picked so the map reads as a legible grid (not a scattering of invisible
# sub-pixel dots, nor a handful of blocky squares) across the whole zoom
# range fine mode can be triggered at, not just the zoom level it happened
# to be tuned against.
#
# Set to 2x the density actually wanted (15), not 15 itself: dashboard.js's
# GEO_FINE_PREFETCH_PAD=0.5 sends a bbox padded 50% on every side (2x the
# span in each dimension, to let panning within already-fetched territory
# skip a re-fetch entirely — see scheduleFineFetch), and this function has
# no way to tell "the bbox I was asked to query" apart from "the bbox that
# actually needs to look dense" — they're the same parameter. Doubling the
# target here compensates so the *visible* viewport (the inner half of
# what's fetched) still ends up at the real target density instead of
# looking twice as coarse as intended. If GEO_FINE_PREFETCH_PAD changes,
# this needs to move with it: target = 15 * (1 + 2 * GEO_FINE_PREFETCH_PAD).
FINE_TARGET_CELLS_ACROSS = 30
FINE_MIN_PRECISION = 4

_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_cell_size_degrees(prefix_len):
    """(lat_size, lon_size) in degrees for a geohash prefix of prefix_len
    characters. Deterministic from length alone — geohash's bit
    interleaving always starts with longitude and strictly alternates, so
    every cell at a given prefix length has exactly this size everywhere on
    Earth (unlike a lat/lon degree grid, where a cell's size *on the
    ground* shrinks toward the poles). That's what lets GeoView report one
    size per response instead of per cell, same as the old grid_size_degrees
    field did for the old fixed-degree grid."""
    n_bits = prefix_len * 5
    lon_bits = (n_bits + 1) // 2
    lat_bits = n_bits // 2
    return 180.0 / (2 ** lat_bits), 360.0 / (2 ** lon_bits)


def geohash_precision_for_bbox(min_lat, min_lon, max_lat, max_lon,
                                target_cells_across=FINE_TARGET_CELLS_ACROSS,
                                min_precision=FINE_MIN_PRECISION, max_precision=GEOHASH_PRECISION):
    """Pick the coarsest geohash prefix length that still gives at least
    `target_cells_across` cells along the *more constrained* of the bbox's
    two dimensions — i.e. the smallest, cheapest precision that still reads
    as a legible grid for this specific viewport, rather than one fixed
    precision for all of resolution=fine.

    A single fixed fine precision (this used to be GEOHASH_PRECISION=6
    unconditionally) cannot be right across the whole range fine mode can
    be triggered at: GeoView's fine/coarse switch fires at one map zoom
    level, but that zoom level's viewport can span anywhere from a small
    country down to a single neighborhood depending on where on Earth (and
    how large a screen) the request comes from. Measured 2026-09-19: at the
    zoom level this project's dashboard switches into fine mode at, a
    precision-6 cell (~1.2km) was under a screen pixel wide across a
    ~1700km viewport — present in the response, invisible on the map, and
    only "discovered" once the user zoomed in much further than the
    threshold that was supposed to already show it."""
    lat_span = max(max_lat - min_lat, 1e-9)
    lon_span = max(max_lon - min_lon, 1e-9)
    for p in range(min_precision, max_precision + 1):
        lat_size, lon_size = geohash_cell_size_degrees(p)
        if min(lat_span / lat_size, lon_span / lon_size) >= target_cells_across:
            return p
    return max_precision


def geohash_decode_center(geohash):
    """Center (lat, lon) of a geohash string or prefix — standard Niemeyer
    geohash bit-interleaving decode (the same scheme PostGIS's ST_GeoHash
    encodes with, so this always matches what's actually stored). Pure
    Python and only ever called on a query's *result* rows (at most a few
    hundred cells) rather than per-row in SQL — see CLAUDE.md's "Geo
    storage: a single geohash key" section for why that's cheaper than
    pushing ST_GeomFromGeoHash into the query."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    is_lon = True
    for ch in geohash:
        idx = _GEOHASH_BASE32.index(ch)
        for bit_pos in (4, 3, 2, 1, 0):
            bit = (idx >> bit_pos) & 1
            if is_lon:
                mid = (lon_lo + lon_hi) / 2.0
                if bit:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2.0
                if bit:
                    lat_lo = mid
                else:
                    lat_hi = mid
            is_lon = not is_lon
    return (lat_lo + lat_hi) / 2.0, (lon_lo + lon_hi) / 2.0


def geohash_encode(lat, lon, precision):
    """Encode (lat, lon) to a geohash string of the given length — inverse
    of geohash_decode_center, same Niemeyer bit-interleaving scheme."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    is_lon = True
    bits = []
    while len(bits) < precision * 5:
        if is_lon:
            mid = (lon_lo + lon_hi) / 2.0
            if lon >= mid:
                bits.append(1)
                lon_lo = mid
            else:
                bits.append(0)
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2.0
            if lat >= mid:
                bits.append(1)
                lat_lo = mid
            else:
                bits.append(0)
                lat_hi = mid
        is_lon = not is_lon
    chars = []
    for i in range(0, len(bits), 5):
        idx = 0
        for bit in bits[i:i + 5]:
            idx = (idx << 1) | bit
        chars.append(_GEOHASH_BASE32[idx])
    return ''.join(chars)


def geohash_bbox_prefix(min_lat, min_lon, max_lat, max_lon, precision=GEOHASH_PRECISION):
    """Longest common geohash prefix covering a bbox's SW/NE corners — used
    to pre-filter a CAgg query that only stores `geohash` (no real geometry
    column to bbox-overlap against, unlike the raw table's GiST-indexed
    `centroid` — see GeoView._from_raw). Combined with
    geohash_prefix_range_sql(), this turns an otherwise full CAgg scan for
    the date range into a sargable range scan, before the exact-bbox crop
    that still has to happen in Python (this prefix only guarantees
    *covering* the bbox, not being tight to it).

    Can degrade to '' (no SQL-level filtering at all) if the bbox straddles
    a major geohash grid boundary — same boundary-discontinuity tradeoff
    already accepted for this whole design (see CLAUDE.md's "Geo storage: a
    single geohash key" section). Still correct either way, just not always
    as fast as it could be for that specific viewport."""
    sw = geohash_encode(min_lat, min_lon, precision)
    ne = geohash_encode(max_lat, max_lon, precision)
    common = []
    for a, b in zip(sw, ne):
        if a != b:
            break
        common.append(a)
    return ''.join(common)
