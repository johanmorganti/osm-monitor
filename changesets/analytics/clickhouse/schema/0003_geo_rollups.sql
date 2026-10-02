-- Map rollups (unfiltered geo_cells), same refreshable pattern and cutoff as
-- daily_rollup (see 0002_rollups.sql for why refreshable).
--
-- The raw table is ordered by time, so a map query reads every geohash in
-- its date range: ~20 s for the full-history world map, 8-11 s for a
-- zoomed-in full-history viewport. Measured on full history:
--   geo_cells_daily   ~117M rows, ~480 MB, ordered by cell: a viewport reads
--                     only the cells covering it (Paris 8 s -> 0.08 s,
--                     France 11.5 s -> 1.8 s);
--   geo_coarse_daily  ~10.7M rows, ~84 MB, at the coarse map's precision
--                     (GEOHASH_PREFIX_LENGTH['coarse'] = 3 in changesets/geo.py),
--                     ordered by day: world map 20 s -> 0.35 s.

-- One row per (full-precision geohash, day).
CREATE TABLE IF NOT EXISTS geo_cells_daily
(
    cell       String CODEC(ZSTD(3)),
    day        Date CODEC(Delta, ZSTD(3)),
    changesets UInt32,
    objects    UInt64 CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY (cell, day);

-- ~3.5 min; the GROUP BY spills to disk past 1.5 GB to stay under the
-- server's memory cap.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo_cells_daily_refresh
REFRESH EVERY 1 DAY OFFSET 5 HOUR
TO geo_cells_daily
AS SELECT
    geohash AS cell,
    toDate(created_at) AS day,
    count() AS changesets,
    sum(changes_count) AS objects
FROM changesets FINAL
WHERE geohash IS NOT NULL AND created_at < toDateTime(today() - 1)
GROUP BY cell, day
SETTINGS max_bytes_before_external_group_by = 1500000000;

-- One row per (day, 3-character cell), derived from geo_cells_daily right
-- after each of its refreshes.
CREATE TABLE IF NOT EXISTS geo_coarse_daily
(
    day        Date,
    cell       String,
    changesets UInt64,
    objects    UInt64
)
ENGINE = MergeTree
ORDER BY (day, cell);

CREATE MATERIALIZED VIEW IF NOT EXISTS geo_coarse_daily_refresh
REFRESH EVERY 1 DAY OFFSET 5 HOUR DEPENDS ON geo_cells_daily_refresh
TO geo_coarse_daily
AS SELECT
    day,
    substring(cell, 1, 3) AS cell,
    sum(changesets) AS changesets,
    sum(objects) AS objects
FROM geo_cells_daily
GROUP BY day, cell;
