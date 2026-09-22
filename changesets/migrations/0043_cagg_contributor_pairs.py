from django.db import migrations


# The 3 remaining dimension pairs, completing the set migration 0041 started
# — contributor x editor/imagery/language. Deferred there specifically
# because of contributor's cardinality (344K distinct values vs. low
# hundreds for the other three — see docs/todo/continuous-aggregates-
# migration.md) and the recurring refresh cost every CAgg adds on this
# I/O-constrained host; built now on request despite that cost, since the
# contributor-grouped toplist was the confirmed remaining slow path.
#
# Same NULL handling as their single-dimension counterparts: contributor
# (user) and editor (created_by_family) exclude NULLs (both are essentially
# always populated in practice), imagery/locale bucket them as the literal
# '(none)' string (CLAUDE.md's "NULL-tag volume gets its own (none) bucket"
# convention).
_CREATE_CONTRIBUTOR_EDITOR_SQL = """
CREATE MATERIALIZED VIEW cagg_contributor_editor_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       "user" AS contributor, created_by_family AS editor,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE "user" IS NOT NULL AND created_by_family IS NOT NULL
GROUP BY bucket, "user", created_by_family
WITH NO DATA;
"""

_CREATE_CONTRIBUTOR_IMAGERY_SQL = """
CREATE MATERIALIZED VIEW cagg_contributor_imagery_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       "user" AS contributor, COALESCE(imagery_family, '(none)') AS imagery,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE "user" IS NOT NULL
GROUP BY bucket, "user", COALESCE(imagery_family, '(none)')
WITH NO DATA;
"""

_CREATE_CONTRIBUTOR_LOCALE_SQL = """
CREATE MATERIALIZED VIEW cagg_contributor_locale_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       "user" AS contributor, COALESCE(locale_family, '(none)') AS locale,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE "user" IS NOT NULL
GROUP BY bucket, "user", COALESCE(locale_family, '(none)')
WITH NO DATA;
"""

_VIEW_NAMES = ['cagg_contributor_editor_daily', 'cagg_contributor_imagery_daily', 'cagg_contributor_locale_daily']


def _policy_sql(view_name):
    # Same cadence as the other pair CAggs (0041) and the single-dimension
    # daily CAggs (0020/0022) — not on the "hot" default-7-day-range path.
    return f"""
SELECT add_continuous_aggregate_policy('{view_name}',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # 0018/0019/0020/0022/0023/0041. CREATE MATERIALIZED VIEW WITH NO DATA
    # is metadata-only regardless of table size — the expensive part
    # (backfilling) is done separately by backfill_dimension_pair_caggs so
    # it can run in small, resumable batches.
    atomic = False

    dependencies = [
        ('changesets', '0042_add_cagg_dimension_pair_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                _CREATE_CONTRIBUTOR_EDITOR_SQL, _policy_sql('cagg_contributor_editor_daily'),
                _CREATE_CONTRIBUTOR_IMAGERY_SQL, _policy_sql('cagg_contributor_imagery_daily'),
                _CREATE_CONTRIBUTOR_LOCALE_SQL, _policy_sql('cagg_contributor_locale_daily'),
            ],
            reverse_sql=[f'DROP MATERIALIZED VIEW IF EXISTS {name};' for name in _VIEW_NAMES],
        ),
    ]
