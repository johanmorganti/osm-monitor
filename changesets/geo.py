"""Geohash helpers for the map (GeoView in changesets/api/views.py, the
ClickHouse backend's geo queries) and the ingest locator
(changesets/ingest/locate.py): one geohash per changeset, coarser cells are
prefixes of finer ones (docs/decisions/geo-geohash.md).

A changeset's bbox (min/max lat/lon) is the envelope of every object it
touched, so a single stray far-away object can balloon it and make the
centroid meaningless. BBOX_DIAG_THRESHOLD_KM excludes those from the map
(they still count everywhere else) rather than plotting them somewhere
misleading. Chosen from a live sample (6 hours, n=3000) of bbox diagonal
size: p50=0.2km, p90=4.3km, p99=49km, p999=235km, max=477km.
"""

BBOX_DIAG_THRESHOLD_KM = 200

# Precision stored per changeset: 6 (~1.2km x 0.61km cells). GeoView resolves
# a requested resolution/zoom to a prefix length in [1, GEOHASH_PRECISION] at
# query time rather than storing several precisions.
GEOHASH_PRECISION = 6

# geohash's own base32 alphabet omits a, i, l, o — '~' sorts after every
# letter and digit, so `geohash >= 'prefix' AND geohash < 'prefix~'` is a
# range scan matching every geohash starting with `prefix`.
GEOHASH_PREFIX_UPPER_BOUND_CHAR = '~'


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


def geohash_bbox_cover(min_lat, min_lon, max_lat, max_lon, max_cells=64, max_precision=GEOHASH_PRECISION):
    """Geohash prefixes whose cells together cover a bbox: the finest
    precision needing at most `max_cells` of them, sorted. Unlike a single
    common prefix ('' as soon as the bbox straddles a top-level boundary, as
    most country-sized viewports do), this stays tight anywhere, so a table
    ordered by geohash can range-scan just these cells. Returns [] for a bbox
    no precision covers within the budget."""
    best = []
    for p in range(1, max_precision + 1):
        lat_size, lon_size = geohash_cell_size_degrees(p)
        lat_steps = int((max_lat + 90) // lat_size) - int((min_lat + 90) // lat_size) + 1
        lon_steps = int((max_lon + 180) // lon_size) - int((min_lon + 180) // lon_size) + 1
        if lat_steps * lon_steps > max_cells:
            break
        cells = set()
        for i in range(lat_steps):
            lat = min(min_lat + i * lat_size, max_lat) if i < lat_steps - 1 else max_lat
            for j in range(lon_steps):
                lon = min(min_lon + j * lon_size, max_lon) if j < lon_steps - 1 else max_lon
                cells.add(geohash_encode(lat, lon, p))
        best = sorted(cells)
    return best
