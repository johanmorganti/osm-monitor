from django.db import migrations


# Rebuilds cagg_imagery_daily/cagg_locale_daily to include NULL-tag volume as
# an explicit "(none)" bucket instead of excluding it (WHERE ... IS NOT NULL,
# from migration 0020). Confirmed via investigation (2026-09-14, TODO.md):
# a ~9,000-changeset MapRoulette campaign with no imagery/locale tags was
# fully invisible in these two breakdowns despite showing up correctly in
# the unfiltered volume chart and the editor breakdown. Continuous
# aggregates can't have their defining query ALTERed in place, so this is a
# drop + recreate (loses materialized data — see the accompanying manual
# backfill, same one-chunk-at-a-time approach as 0020's original backfill).
# cagg_editor_daily/cagg_contributor_daily are left as-is: created_by_family
# and user are essentially always populated in practice (confirmed during
# the same investigation), so there's no equivalent real gap to fix there,
# and rebuilding them would mean redoing their backfill for no benefit.
_DIMENSIONS = [
    ('cagg_imagery_daily', 'imagery_family'),
    ('cagg_locale_daily', 'locale_family'),
]


def _create_sql(view_name, column):
    return f"""
CREATE MATERIALIZED VIEW {view_name} WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket, COALESCE({column}, '(none)') AS name,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, COALESCE({column}, '(none)')
WITH NO DATA;
"""


def _create_sql_old(view_name, column):
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


def _drop_sql(view_name):
    return f"DROP MATERIALIZED VIEW IF EXISTS {view_name};"


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('changesets', '0021_add_cagg_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                sql
                for view_name, column in _DIMENSIONS
                for sql in (_drop_sql(view_name), _create_sql(view_name, column), _policy_sql(view_name))
            ],
            reverse_sql=[
                sql
                for view_name, column in _DIMENSIONS
                for sql in (_drop_sql(view_name), _create_sql_old(view_name, column), _policy_sql(view_name))
            ],
        ),
    ]
