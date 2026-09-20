from django.db import migrations, models
from django.db.models.functions import Upper


# AddIndexConcurrently doesn't work here — TimescaleDB rejects
# CREATE INDEX CONCURRENTLY issued directly against a hypertable
# ("hypertables do not support concurrent index creation"), unlike the plain
# table this same expression index type was built against for user/
# created_by_family/imagery_family in migration 0015 (that ran *before*
# f01aabd converted changesets_changeset to a hypertable). And unlike
# migration 0032's geohash/country_code indexes, a plain non-concurrent
# CREATE INDEX isn't an option either: those were built immediately after
# ADD COLUMN with every existing row still NULL, so there was no data to
# scan; locale_family is already populated for the whole table, so a
# non-concurrent build would hold an ACCESS EXCLUSIVE lock on
# changesets_changeset for as long as the scan takes — blocking the poller
# and every dashboard query on this I/O-constrained host for real time.
#
# Workaround (documented Timescale pattern for adding an index to an
# already-populated hypertable without locking it): build the same
# expression index CONCURRENTLY on each chunk individually — a chunk is a
# regular table, so CONCURRENTLY is fully supported there and only takes a
# lock on that one chunk — then register it once at the hypertable level.
# That final CREATE INDEX finds matching indexes already present on every
# chunk and just links them into the catalog rather than rescanning any
# data, so it's fast despite not being CONCURRENTLY itself.
def _create_locale_upper_index(apps, schema_editor):
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
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{chunk_name}_locale_upper_idx" '
                f'ON "{chunk_schema}"."{chunk_name}" (UPPER(locale_family))'
            )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS changeset_locale_upper_idx '
            'ON changesets_changeset (UPPER(locale_family))'
        )


def _drop_locale_upper_index(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DROP INDEX IF EXISTS changeset_locale_upper_idx')


class Migration(migrations.Migration):
    # Required for CONCURRENTLY (see above) — same reasoning as 0015/0032/0036.
    atomic = False

    dependencies = [
        ('changesets', '0039_remove_importjob'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='changeset',
                    index=models.Index(Upper('locale_family'), name='changeset_locale_upper_idx'),
                ),
            ],
            database_operations=[
                migrations.RunPython(_create_locale_upper_index, _drop_locale_upper_index),
            ],
        ),
    ]
