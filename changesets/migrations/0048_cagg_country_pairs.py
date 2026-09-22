from django.db import migrations


# The 3 country pairs requested alongside the country dimension itself —
# country x editor, country x imagery, country x contributor — same shape
# and NULL handling as the existing 6 pairs (migrations 0041/0043).
# country x language deliberately not built: language is being retired
# from the dashboard in the same change, so a language-crossed country
# pair has no current caller.
#
# Naming: contributor still leads when it's part of the pair (matching
# cagg_contributor_editor_daily/cagg_contributor_imagery_daily/
# cagg_contributor_locale_daily's precedent); country/editor and
# country/imagery are alphabetical, matching cagg_editor_imagery_daily's
# precedent for the other same-cardinality-class pairs.
_CREATE_CONTRIBUTOR_COUNTRY_SQL = """
CREATE MATERIALIZED VIEW cagg_contributor_country_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       "user" AS contributor, COALESCE(country_code, '(none)') AS country,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE "user" IS NOT NULL
GROUP BY bucket, "user", COALESCE(country_code, '(none)')
WITH NO DATA;
"""

_CREATE_COUNTRY_EDITOR_SQL = """
CREATE MATERIALIZED VIEW cagg_country_editor_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       COALESCE(country_code, '(none)') AS country, created_by_family AS editor,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE created_by_family IS NOT NULL
GROUP BY bucket, COALESCE(country_code, '(none)'), created_by_family
WITH NO DATA;
"""

_CREATE_COUNTRY_IMAGERY_SQL = """
CREATE MATERIALIZED VIEW cagg_country_imagery_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       COALESCE(country_code, '(none)') AS country, COALESCE(imagery_family, '(none)') AS imagery,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, COALESCE(country_code, '(none)'), COALESCE(imagery_family, '(none)')
WITH NO DATA;
"""

_VIEW_NAMES = ['cagg_contributor_country_daily', 'cagg_country_editor_daily', 'cagg_country_imagery_daily']


def _policy_sql(view_name):
    return f"""
SELECT add_continuous_aggregate_policy('{view_name}',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""


class Migration(migrations.Migration):
    # Same reasoning as every other CAgg migration — CREATE MATERIALIZED
    # VIEW WITH NO DATA is metadata-only; the expensive backfill is done
    # separately (see backfill_country_caggs) so it can run in small,
    # resumable batches.
    atomic = False

    dependencies = [
        ('changesets', '0047_changeset_country_upper_index'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                _CREATE_CONTRIBUTOR_COUNTRY_SQL, _policy_sql('cagg_contributor_country_daily'),
                _CREATE_COUNTRY_EDITOR_SQL, _policy_sql('cagg_country_editor_daily'),
                _CREATE_COUNTRY_IMAGERY_SQL, _policy_sql('cagg_country_imagery_daily'),
            ],
            reverse_sql=[f'DROP MATERIALIZED VIEW IF EXISTS {name};' for name in _VIEW_NAMES],
        ),
    ]
