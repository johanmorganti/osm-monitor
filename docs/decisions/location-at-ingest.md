# Location data is computed at ingest, not in the database (2026-10-01)

> **2026-10-05:** TimescaleDB and Postgres were removed ([timescale-removal.md](timescale-removal.md)); what this says about them (CAggs, the hypertable, Postgres queries) is history, the rest still holds.

`geohash` and `country_code` are computed by the ingest parser (`changesets/ingest/locate.py`,
called from `import_changeset_batch`), not by a Postgres trigger. The trigger (migration 0057)
only derives `centroid`, the PostGIS geometry the Timescale backend's raw map queries use for
their GiST-indexed viewport filter, and only when the parser produced a geohash, so the parser
alone decides whether a bbox is trustworthy.

**Why:** every analytics backend has to get identical locations without each one needing its own
geo stack (see [analytics-backends.md](analytics-backends.md)). The rules are the trigger's, unchanged: the bbox center,
only when the bbox is present, within ±180/±90 and at most `BBOX_DIAG_THRESHOLD_KM` across
(geodesic, WGS84); geohash at `GEOHASH_PRECISION`; country = the containing polygon (smallest ISO
code on overlaps), else the nearest within 5 km. Parity measured before switching, on 3.17M rows
over 400 random days: geohash identical everywhere, country different on 13 rows (0.0004%), all at
the 5 km cutoff or between two near-equidistant neighbors. That's where PostGIS's geodesic polygon
edges and the GeoJSON's straight lon/lat edges diverge on the simplified borders' long edges; the
Python side follows the GeoJSON source.

**How to apply:** change location rules in `locate.py` only, then run `recompute_locations
--check` (samples random days and reports differences against stored values) and, if the change is
intended, `recompute_locations --start … --end …` to rewrite the affected rows. The
`country_boundaries` / `country_boundaries_subdivided` tables are no longer read at ingest.
