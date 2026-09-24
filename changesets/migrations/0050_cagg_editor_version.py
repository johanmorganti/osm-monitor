from django.db import migrations


# Editor family -> exact created_by version string within it (e.g.
# "StreetComplete" -> "StreetComplete 55.0") — backs ToplistView's
# dimension=editor_version drill-down (requires an `editor` filter to stay
# bounded, see views.py's RAW_ONLY_DIMENSIONS). NULLs excluded outright,
# not COALESCE'd to '(none)' — same choice already made for
# cagg_editor_daily/cagg_contributor_daily: created_by/created_by_family
# are essentially always populated in practice, unlike imagery/language/
# country, so there's no equivalent "large NULL campaign" gap to protect
# against here (see migration 0022's own comment on that).
_CREATE_SQL = """
CREATE MATERIALIZED VIEW cagg_editor_version_daily WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', created_at) AS bucket,
       created_by_family AS editor, created_by AS version,
       count(*) AS cnt, COALESCE(sum(changes_count), 0) AS changes_sum
FROM changesets_changeset
WHERE created_by_family IS NOT NULL AND created_by IS NOT NULL
GROUP BY bucket, created_by_family, created_by
WITH NO DATA;
"""

_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('cagg_editor_version_daily',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
"""


class Migration(migrations.Migration):
    # CREATE MATERIALIZED VIEW ... WITH NO DATA + policy registration are
    # Timescale catalog operations, kept out of one transaction — same
    # reason as every other CAgg migration (0018/.../0045/0048). The
    # expensive backfill is a separate, resumable step — see
    # backfill_editor_version_cagg.py — not part of this migration.
    atomic = False

    dependencies = [
        ('changesets', '0049_add_cagg_country_pair_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_CREATE_SQL, _POLICY_SQL],
            reverse_sql=['DROP MATERIALIZED VIEW IF EXISTS cagg_editor_version_daily;'],
        ),
    ]
