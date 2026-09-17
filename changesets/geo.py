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
# but this host's DB is slow enough under load (see TODO.md) that a second
# CAgg — paid for in storage, not query time — is the better trade here.
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
