from django.db import migrations

from changesets.geo import FINE_GRID_LAT_GATED_SQL, FINE_GRID_LON_GATED_SQL


# Second geo CAgg, at a finer grid (see changesets/geo.py's FINE_GRID_SIZE_DEGREES,
# ~0.05°/5.5km vs. cagg_geo_daily's 0.5°/55km) — backs GeoView's "fine"
# resolution for zoomed-in map views. Viewport-scoped at query time
# (GeoView filters by grid_lat/grid_lon range), not date-range-only like
# the coarse CAgg, since even pre-aggregated, shipping every fine cell on
# Earth would be excessive payload for one zoomed-in view.
#
# Pre-aggregated rather than computed live from the raw table per-request:
# an earlier design did the latter (viewport + date bounded raw query), but
# this host's DB is slow enough under concurrent/large scans (see TODO.md)
# that paying the cost in storage via a second CAgg, not query latency, is
# the better trade — this host has disk space to spare.
_CREATE_SQL = f"""
CREATE MATERIALIZED VIEW cagg_geo_fine_daily WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', created_at) AS bucket,
    {FINE_GRID_LAT_GATED_SQL} AS grid_lat,
    {FINE_GRID_LON_GATED_SQL} AS grid_lon,
    count(*) AS cnt,
    COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE min_lat IS NOT NULL AND max_lat IS NOT NULL AND min_lon IS NOT NULL AND max_lon IS NOT NULL
GROUP BY bucket, {FINE_GRID_LAT_GATED_SQL}, {FINE_GRID_LON_GATED_SQL}
WITH NO DATA;
"""

# 1-hour cadence, matching cagg_geo_daily and the other daily CAs — no
# reason for a finer-grid CAgg to refresh more often than its coarse
# counterpart.
_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_geo_fine_daily',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""

_DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS cagg_geo_fine_daily;"


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # 0018/0019/0020/0022/0023/0025.
    atomic = False

    dependencies = [
        ('changesets', '0026_add_geo_cagg_model'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_SQL, _POLICY_SQL],
            reverse_sql=[_DROP_SQL],
        ),
    ]
