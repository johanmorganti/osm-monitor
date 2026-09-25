from django.db import migrations

from changesets.geo import GRID_LAT_GATED_SQL, GRID_LON_GATED_SQL


# New continuous aggregate for the dashboard's changeset-density heatmap
# (see the "Changeset activity map" plan). Grid-cell (grid_lat/grid_lon) and
# bbox-quality-gate expressions live in changesets/geo.py, shared with
# GeoView's raw-fallback path in views.py — see that module's docstring for
# the reasoning and the empirical basis for the 200km threshold.
#
# No new column, no ALTER TABLE, no backfill against the raw
# changesets_changeset hypertable: the grid/quality logic is entirely in
# this CA's SELECT, the same low-risk DDL pattern as 0020/0022/0023 (a
# materialized view registration, not a scan of the base table). The
# backfill this CA still needs (like every other CA here) is a manual,
# month-by-month `CALL refresh_continuous_aggregate('cagg_geo_daily', ...)`
# after this migration lands — not part of the migration itself.
_CREATE_SQL = f"""
CREATE MATERIALIZED VIEW cagg_geo_daily WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    {GRID_LAT_GATED_SQL} AS grid_lat,
    {GRID_LON_GATED_SQL} AS grid_lon,
    count(*) AS cnt,
    COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE min_lat IS NOT NULL AND max_lat IS NOT NULL AND min_lon IS NOT NULL AND max_lon IS NOT NULL
GROUP BY bucket, {GRID_LAT_GATED_SQL}, {GRID_LON_GATED_SQL}
WITH NO DATA;
"""

# 1-hour cadence, matching the existing per-dimension DAILY CAs (not the
# hourly ones' 30-minute cadence) — a heatmap doesn't need near-real-time
# freshness, and this avoids adding another frequent background job.
_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_geo_daily',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""

_DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS cagg_geo_daily;"


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # 0018/0019/0020/0022/0023.
    atomic = False

    dependencies = [
        ('changesets', '0024_add_hourly_cagg_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_SQL, _POLICY_SQL],
            reverse_sql=[_DROP_SQL],
        ),
    ]
