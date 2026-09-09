from django.db import migrations, models


class Migration(migrations.Migration):
    # DDL here (extension creation, hypertable conversion) shouldn't be
    # nested in one outer transaction.
    atomic = False

    dependencies = [
        ('changesets', '0017_alter_filtervalue_id'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='changeset',
                    name='changeset_id',
                    field=models.BigIntegerField(),
                ),
                migrations.AddConstraint(
                    model_name='changeset',
                    constraint=models.UniqueConstraint(fields=['changeset_id', 'created_at'], name='changeset_changeset_id_created_at_uniq'),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        "CREATE EXTENSION IF NOT EXISTS timescaledb;",
                        "ALTER TABLE changesets_changeset DROP CONSTRAINT changesets_changeset_pkey;",
                        "ALTER TABLE changesets_changeset DROP CONSTRAINT changesets_changeset_changeset_id_key;",
                        "SELECT create_hypertable('changesets_changeset', 'created_at', chunk_time_interval => INTERVAL '1 month', migrate_data => true);",
                        "ALTER TABLE changesets_changeset ADD CONSTRAINT changeset_changeset_id_created_at_uniq UNIQUE (changeset_id, created_at);",
                        # id is no longer a PRIMARY KEY at the DB level (Timescale
                        # requires the partitioning column in any unique
                        # constraint) — dropping the PK constraint also dropped
                        # its backing index, so add a plain (non-unique) one
                        # back for lookup performance; Django still treats id
                        # as `pk` at the ORM level regardless.
                        "CREATE INDEX IF NOT EXISTS changesets_changeset_id_idx ON changesets_changeset (id);",
                    ],
                    reverse_sql=[
                        "DROP INDEX IF EXISTS changesets_changeset_id_idx;",
                        "ALTER TABLE changesets_changeset DROP CONSTRAINT IF EXISTS changeset_changeset_id_created_at_uniq;",
                        "ALTER TABLE changesets_changeset ADD CONSTRAINT changesets_changeset_changeset_id_key UNIQUE (changeset_id);",
                        "ALTER TABLE changesets_changeset ADD CONSTRAINT changesets_changeset_pkey PRIMARY KEY (id);",
                        # Not attempting to un-convert the hypertable back to a
                        # plain table — this migration is only meant to be
                        # reversed on a fresh/empty database.
                    ],
                ),
            ],
        ),
    ]
