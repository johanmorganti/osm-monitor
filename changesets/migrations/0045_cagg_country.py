from django.db import migrations


# Single-dimension country CAggs, same shape as cagg_locale_daily/hourly
# (migrations 0022/0023) — country_code can legitimately be NULL (open
# ocean, or one of the ~20 disputed/unmapped territories excluded from
# country_boundaries — see migration 0032/CLAUDE.md), so it gets the same
# explicit '(none)' bucket rather than being excluded (CLAUDE.md's
# "NULL-tag volume gets its own (none) bucket" convention).
#
# country_code has existed on Changeset since migration 0032 (schema +
# full-history backfill), but was deliberately left with no DIMENSION_FIELDS
# entry, no CAgg, no dashboard wiring at the time — see that migration's
# comment and CLAUDE.md's "Geo storage" section. This migration is the
# "actually wire it up as a real dimension" step, replacing `language` on
# the dashboard (language stays a valid API param — not touched here).
_CREATE_DAILY_SQL = """
CREATE MATERIALIZED VIEW cagg_country_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket, COALESCE(country_code, '(none)') AS name,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, COALESCE(country_code, '(none)')
WITH NO DATA;
"""

_CREATE_HOURLY_SQL = """
CREATE MATERIALIZED VIEW cagg_country_hourly WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', created_at) AS bucket, COALESCE(country_code, '(none)') AS name,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
GROUP BY bucket, COALESCE(country_code, '(none)')
WITH NO DATA;
"""

_POLICY_DAILY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_country_daily',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""

_POLICY_HOURLY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_country_hourly',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '30 minutes');
"""


class Migration(migrations.Migration):
    # Continuous aggregate creation + policy registration are Timescale
    # catalog operations, kept out of one transaction — same reason as
    # every other CAgg migration (0018/0019/0020/0022/0023/0041/0043).
    atomic = False

    dependencies = [
        ('changesets', '0044_add_cagg_contributor_pair_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_DAILY_SQL, _POLICY_DAILY_SQL, _CREATE_HOURLY_SQL, _POLICY_HOURLY_SQL],
            reverse_sql=[
                'DROP MATERIALIZED VIEW IF EXISTS cagg_country_daily;',
                'DROP MATERIALIZED VIEW IF EXISTS cagg_country_hourly;',
            ],
        ),
    ]
