from django.db import migrations, models
from django.db.models.functions import Upper


# Same reasoning and same per-chunk CONCURRENTLY workaround as migration
# 0040's changeset_locale_upper_idx: the raw-table fallback (ToplistView/
# GeoView/TimeseriesView) filters country the same __iexact way every other
# dimension does, so it needs a matching UPPER() expression index to stay
# sargable — added proactively here, before country's raw fallback ever
# gets exercised at a wide date range, rather than waiting to hit the same
# missing-index timeout bug language did. country_code is already populated
# for the whole table (migration 0032's backfill), so — same as locale —
# a plain CREATE INDEX would hold an ACCESS EXCLUSIVE lock for as long as
# the scan takes; TimescaleDB also rejects CREATE INDEX CONCURRENTLY issued
# directly against a hypertable. Build per-chunk CONCURRENTLY, then
# register once at the hypertable level (fast — finds the matching chunk
# indexes already in place rather than rescanning).
def _create_country_upper_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT chunk_schema, chunk_name FROM timescaledb_information.chunks "
            "WHERE hypertable_name = 'changesets_changeset'"
        )
        chunks = cursor.fetchall()
        for chunk_schema, chunk_name in chunks:
            cursor.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{chunk_name}_country_upper_idx" '
                f'ON "{chunk_schema}"."{chunk_name}" (UPPER(country_code))'
            )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS changeset_country_upper_idx '
            'ON changesets_changeset (UPPER(country_code))'
        )


def _drop_country_upper_index(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DROP INDEX IF EXISTS changeset_country_upper_idx')


class Migration(migrations.Migration):
    # Required for CONCURRENTLY (see above) — same reasoning as 0015/0032/
    # 0036/0040.
    atomic = False

    dependencies = [
        ('changesets', '0046_add_cagg_country_models'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='changeset',
                    index=models.Index(Upper('country_code'), name='changeset_country_upper_idx'),
                ),
            ],
            database_operations=[
                migrations.RunPython(_create_country_upper_index, _drop_country_upper_index),
            ],
        ),
    ]
