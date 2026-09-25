from django.db import migrations


# Three new daily CAggs answering "filter by one of {editor, imagery,
# language}, broken out by another" directly — the shape ToplistView's
# dimension param and TimeseriesView's group_by (combined with a filter)
# both need, and that no single-dimension CAgg can answer (see
# docs/todo/continuous-aggregates-migration.md's "Real, confirmed remaining
# gap"). Deliberately only the 3 pairs *not* involving contributor: that
# dimension has 344K distinct values (vs. low hundreds for the other three,
# confirmed 2026-09-20) — a contributor-crossed pair CAgg wouldn't explode
# combinatorially in row count (most contributors stick to ~1 editor/
# imagery/language), but every CAgg also carries a recurring refresh cost
# every time its policy fires, not just a one-time build cost. Deferred,
# documented, not built here — see that same doc, along with GeoView's equivalent gap (dimension x geohash, a
# different shape entirely since geo isn't grouped by another attribute).
#
# NULL handling matches each dimension's existing single-dimension CAgg
# (CLAUDE.md's "NULL-tag volume gets its own (none) bucket" convention):
# editor (created_by_family) excludes NULLs, imagery/locale bucket them as
# the literal '(none)' string.
_CREATE_EDITOR_IMAGERY_SQL = """
CREATE MATERIALIZED VIEW cagg_editor_imagery_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       created_by_family AS editor, COALESCE(imagery_family, '(none)') AS imagery,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE created_by_family IS NOT NULL
GROUP BY bucket, created_by_family, COALESCE(imagery_family, '(none)')
WITH NO DATA;
"""

_CREATE_EDITOR_LOCALE_SQL = """
CREATE MATERIALIZED VIEW cagg_editor_locale_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       created_by_family AS editor, COALESCE(locale_family, '(none)') AS locale,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE created_by_family IS NOT NULL
GROUP BY bucket, created_by_family, COALESCE(locale_family, '(none)')
WITH NO DATA;
"""

_CREATE_IMAGERY_LOCALE_SQL = """
CREATE MATERIALIZED VIEW cagg_imagery_locale_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       COALESCE(imagery_family, '(none)') AS imagery, COALESCE(locale_family, '(none)') AS locale,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, COALESCE(imagery_family, '(none)'), COALESCE(locale_family, '(none)')
WITH NO DATA;
"""

_VIEW_NAMES = ['cagg_editor_imagery_daily', 'cagg_editor_locale_daily', 'cagg_imagery_locale_daily']


def _policy_sql(view_name):
    # Same cadence as the single-dimension daily CAggs (0020/0022) — these
    # aren't on the "hot" default-7-day-range path the hourly ones are.
    return f"""
SELECT add_continuous_aggregate_policy('{view_name}',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # 0018/0019/0020/0022/0023. Unlike migration 0040's index, creating a
    # materialized view WITH NO DATA is metadata-only (no data copy, no
    # lock of consequence) regardless of table size — the expensive part is
    # backfilling it after the fact, done separately by the
    # backfill_dimension_pair_caggs management command so it can run in
    # small, resumable batches rather than inside this migration.
    atomic = False

    dependencies = [
        ('changesets', '0040_changeset_locale_upper_index'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                _CREATE_EDITOR_IMAGERY_SQL, _policy_sql('cagg_editor_imagery_daily'),
                _CREATE_EDITOR_LOCALE_SQL, _policy_sql('cagg_editor_locale_daily'),
                _CREATE_IMAGERY_LOCALE_SQL, _policy_sql('cagg_imagery_locale_daily'),
            ],
            reverse_sql=[f'DROP MATERIALIZED VIEW IF EXISTS {name};' for name in _VIEW_NAMES],
        ),
    ]
