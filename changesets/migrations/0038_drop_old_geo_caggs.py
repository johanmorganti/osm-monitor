from django.db import migrations


# Retires cagg_geo_daily/cagg_geo_fine_daily (migrations 0025/0027) now that
# GeoView reads from the single geohash-keyed cagg_geo_hashed_daily
# (migration 0033) instead — see docs/todo/geo-spatial-key-and-country.md
# for the full design rationale and the 2026-09-19 spot-verification this
# migration follows (same cell counts/totals as the two CAggs it replaces,
# compared over several shared date ranges before this was applied).
#
# Recreating either dropped CAgg from its own reverse_sql (below) restores
# an *empty* materialized view, not its data — matching every other
# hypertable/CAgg migration in this project's stance that a migration
# reverse is for a fresh/empty database, not for undoing a real drop (see
# 0018/0019/0030's identical reasoning). Re-backfilling either one for real
# would mean redoing the multi-hour chunk-by-chunk CALL
# refresh_continuous_aggregate pass this migration exists to make
# unnecessary.
_DROP_SQL = [
    "DROP MATERIALIZED VIEW IF EXISTS cagg_geo_daily;",
    "DROP MATERIALIZED VIEW IF EXISTS cagg_geo_fine_daily;",
]

# Verbatim defining queries from migrations 0025/0027 — kept here only so
# `reverse_sql` can recreate the (empty) views; not used by any view code.
_RECREATE_COARSE_SQL = """
CREATE MATERIALIZED VIEW cagg_geo_daily WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    CASE WHEN sqrt(power((max_lat - min_lat) * 111.0, 2) + power((max_lon - min_lon) * 111.0 * cos(radians((min_lat + max_lat) / 2.0)), 2)) <= 200
         THEN round(((min_lat + max_lat) / 2.0) / 0.5) * 0.5 ELSE NULL END AS grid_lat,
    CASE WHEN sqrt(power((max_lat - min_lat) * 111.0, 2) + power((max_lon - min_lon) * 111.0 * cos(radians((min_lat + max_lat) / 2.0)), 2)) <= 200
         THEN round(((min_lon + max_lon) / 2.0) / 0.5) * 0.5 ELSE NULL END AS grid_lon,
    count(*) AS cnt,
    COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, grid_lat, grid_lon
WITH NO DATA;
"""

_RECREATE_FINE_SQL = """
CREATE MATERIALIZED VIEW cagg_geo_fine_daily WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    CASE WHEN sqrt(power((max_lat - min_lat) * 111.0, 2) + power((max_lon - min_lon) * 111.0 * cos(radians((min_lat + max_lat) / 2.0)), 2)) <= 200
         THEN round(((min_lat + max_lat) / 2.0) / 0.05) * 0.05 ELSE NULL END AS grid_lat,
    CASE WHEN sqrt(power((max_lat - min_lat) * 111.0, 2) + power((max_lon - min_lon) * 111.0 * cos(radians((min_lat + max_lat) / 2.0)), 2)) <= 200
         THEN round(((min_lon + max_lon) / 2.0) / 0.05) * 0.05 ELSE NULL END AS grid_lon,
    count(*) AS cnt,
    COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, grid_lat, grid_lon
WITH NO DATA;
"""

_RECREATE_SQL = [_RECREATE_COARSE_SQL, _RECREATE_FINE_SQL]


class Migration(migrations.Migration):
    # CAgg DDL, kept out of one transaction — same reason as every other
    # CAgg migration here (0018/0019/0020/0022/0023/0025/0027/0033).
    atomic = False

    dependencies = [
        ('changesets', '0037_changeset_geohash_country_code_state'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='CaggGeoDaily'),
                migrations.DeleteModel(name='CaggGeoFineDaily'),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=_DROP_SQL,
                    reverse_sql=_RECREATE_SQL,
                ),
            ],
        ),
    ]
