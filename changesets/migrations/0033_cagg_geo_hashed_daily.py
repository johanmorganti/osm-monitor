from django.db import migrations


# Replaces the two-CAgg/two-column (grid_lat/grid_lon) geo design
# (cagg_geo_daily, cagg_geo_fine_daily — migrations 0025/0027) with a
# single CAgg keyed by the geohash column (migration 0032). See
# docs/todo/geo-spatial-key-and-country.md for the measured problem this
# solves (2.9s / 569 random page reads for a one-month, tight-viewport
# query, root-caused to two independent 1-D indexes for a 2-D question).
#
# One stored key serves every zoom level: a coarser cell is just a shorter
# *prefix* of a finer one (geohash's nesting property — confirmed live:
# ST_GeoHash(point, 3) is a prefix of ST_GeoHash(point, 6) for the same
# point), so GeoView can truncate this column to whatever resolution a
# request needs instead of choosing between two pre-materialized grids.
#
# WHERE geohash IS NOT NULL, not COALESCE-to-a-sentinel like the imagery/
# language "(none)" bucket (migration 0022): a changeset with no trustworthy
# centroid has no meaningful location at any resolution, unlike an
# untagged-imagery changeset which still has *a* real answer ("no imagery
# tag"). Matches the old two-CAgg design's NULL-exclusion, not the
# NONE_BUCKET one.
_CREATE_SQL = """
CREATE MATERIALIZED VIEW cagg_geo_hashed_daily WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    geohash,
    count(*) AS cnt,
    COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE geohash IS NOT NULL
GROUP BY bucket, geohash
WITH NO DATA;
"""

# 1-hour cadence, matching the CAggs it replaces.
_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_geo_hashed_daily',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""

# Compression, set up front rather than deferred like the raw table's
# backlog (CLAUDE.md's "Compression" section) — this CAgg is small (a geohash
# CAgg over the full 2005-2026 history is expected in the same order as the
# old fine CAgg's 6.5M rows) and, critically, `orderby` here isn't only
# about compression ratio: TimescaleDB's compressed format stores a sparse
# per-compressed-batch min/max index on the orderby columns, so a query
# filtering on a geohash *range* (the sargable prefix-range trick — see
# changesets/geo.py's geohash_prefix_range_sql) can skip whole compressed
# batches that don't overlap, rather than visiting scattered individual heap
# pages. That's a direct, structural answer to the 569-random-seek number
# this migration exists to fix — physical clustering by the query's own key,
# not just an index atop unclustered storage.
_COMPRESS_SQL = """
ALTER MATERIALIZED VIEW cagg_geo_hashed_daily SET (
  timescaledb.compress = true,
  timescaledb.compress_orderby = 'geohash, bucket DESC'
);
"""

_COMPRESSION_POLICY_SQL = """
SELECT add_compression_policy('cagg_geo_hashed_daily', INTERVAL '7 days');
"""

_DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS cagg_geo_hashed_daily;"


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # every other CAgg migration here (0018/0019/0020/0022/0023/0025/0027).
    atomic = False

    dependencies = [
        ('changesets', '0032_geohash_and_country_code'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_SQL, _POLICY_SQL, _COMPRESS_SQL, _COMPRESSION_POLICY_SQL],
            reverse_sql=[_DROP_SQL],
        ),
    ]
