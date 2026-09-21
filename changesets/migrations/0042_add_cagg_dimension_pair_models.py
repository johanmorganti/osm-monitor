from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: every model here has managed=False (mapped onto a
    # continuous aggregate view created directly by migration 0041's raw
    # SQL), so Django issues no DDL for these CreateModel operations — this
    # just teaches makemigrations/the ORM about a schema it doesn't own,
    # same pattern as 0021 for the single-dimension CAggs.

    dependencies = [
        ('changesets', '0041_cagg_dimension_pairs'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggEditorImageryDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('editor', models.CharField(max_length=255)),
                ('imagery', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_editor_imagery_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggEditorLocaleDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('editor', models.CharField(max_length=255)),
                ('locale', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_editor_locale_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggImageryLocaleDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('imagery', models.CharField(max_length=255)),
                ('locale', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_imagery_locale_daily',
                'managed': False,
            },
        ),
    ]
