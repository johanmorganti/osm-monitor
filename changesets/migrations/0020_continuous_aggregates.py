from django.db import migrations


_DIMENSIONS = [
    ('cagg_editor_daily', 'created_by_family'),
    ('cagg_imagery_daily', 'imagery_family'),
    ('cagg_locale_daily', 'locale_family'),
    ('cagg_contributor_daily', '"user"'),
]

_CREATE_VOLUME_SQL = """
CREATE MATERIALIZED VIEW cagg_volume_hourly WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', created_at) AS bucket,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset GROUP BY bucket
WITH NO DATA;
"""

_POLICY_VOLUME_SQL = """
SELECT add_continuous_aggregate_policy('cagg_volume_hourly',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '30 minutes');
"""


def _create_dimension_sql(view_name, column):
    return f"""
CREATE MATERIALIZED VIEW {view_name} WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket, {column} AS name,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset WHERE {column} IS NOT NULL
GROUP BY bucket, {column}
WITH NO DATA;
"""


def _policy_sql(view_name):
    return f"""
SELECT add_continuous_aggregate_policy('{view_name}',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction for the same reason
    # as 0018's hypertable conversion and 0019's compression policy.
    atomic = False

    dependencies = [
        ('changesets', '0019_compress_changesets'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_VOLUME_SQL, _POLICY_VOLUME_SQL] + [
                sql
                for view_name, column in _DIMENSIONS
                for sql in (_create_dimension_sql(view_name, column), _policy_sql(view_name))
            ],
            reverse_sql=[
                "DROP MATERIALIZED VIEW IF EXISTS cagg_volume_hourly;",
            ] + [
                f"DROP MATERIALIZED VIEW IF EXISTS {view_name};"
                for view_name, _ in _DIMENSIONS
            ],
        ),
    ]
