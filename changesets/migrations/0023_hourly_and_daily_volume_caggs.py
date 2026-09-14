from django.db import migrations


# Closes two gaps confirmed this session in TimeseriesView (see TODO.md):
# 1. The ungrouped path only ever had cagg_volume_hourly, hard-sliced [:360]
#    — any range wider than 15 days silently returned just the oldest 15
#    days with no indication. cagg_volume_daily gives it a coarser grain to
#    fall back to instead of truncating.
# 2. The grouped (group_by=...) path only ever had daily-grain per-dimension
#    CAs, with no cap at all — a multi-year range would return thousands of
#    points per series, and narrow ranges couldn't show hourly detail (the
#    concrete case that started this: the 2026-08-30 MapRoulette spike,
#    diluted across a whole day at daily grain). One hourly CA per dimension
#    gives TimeseriesView's new _pick_interval() a real choice to make.
#
# Each hourly dimension CA mirrors its daily counterpart's exact NULL
# handling (0020_continuous_aggregates.py / 0022_cagg_imagery_locale_none_bucket.py):
# editor/contributor exclude NULLs (created_by_family/user are essentially
# always populated), imagery/locale bucket NULLs as an explicit '(none)'
# name (same reasoning as the 0022 fix — a large untagged campaign should
# still be visible, not silently dropped).
_HOURLY_DIMENSIONS = [
    ('cagg_editor_hourly', 'created_by_family', False),
    ('cagg_imagery_hourly', 'imagery_family', True),
    ('cagg_locale_hourly', 'locale_family', True),
    ('cagg_contributor_hourly', '"user"', False),
]

_CREATE_VOLUME_DAILY_SQL = """
CREATE MATERIALIZED VIEW cagg_volume_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset GROUP BY bucket
WITH NO DATA;
"""

_POLICY_VOLUME_DAILY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_volume_daily',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""


def _create_hourly_sql(view_name, column, none_bucket):
    select_name = f"COALESCE({column}, '(none)')" if none_bucket else column
    where = '' if none_bucket else f'WHERE {column} IS NOT NULL'
    return f"""
CREATE MATERIALIZED VIEW {view_name} WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', created_at) AS bucket, {select_name} AS name,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset {where}
GROUP BY bucket, {select_name}
WITH NO DATA;
"""


def _policy_sql(view_name):
    # 30-minute cadence, not the daily CAs' 1 hour — matching cagg_volume_hourly's
    # own cadence, since these become part of the "hot" default-range path
    # (the dashboard's 7-day default range auto-picks hourly).
    return f"""
SELECT add_continuous_aggregate_policy('{view_name}',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '30 minutes');
"""


def _drop_sql(view_name):
    return f'DROP MATERIALIZED VIEW IF EXISTS {view_name};'


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # 0018/0019/0020/0022.
    atomic = False

    dependencies = [
        ('changesets', '0022_cagg_imagery_locale_none_bucket'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_VOLUME_DAILY_SQL, _POLICY_VOLUME_DAILY_SQL] + [
                sql
                for view_name, column, none_bucket in _HOURLY_DIMENSIONS
                for sql in (_create_hourly_sql(view_name, column, none_bucket), _policy_sql(view_name))
            ],
            reverse_sql=[
                _drop_sql('cagg_volume_daily'),
            ] + [
                _drop_sql(view_name)
                for view_name, _, _ in _HOURLY_DIMENSIONS
            ],
        ),
    ]
