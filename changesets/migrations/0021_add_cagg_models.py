from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: every model here has managed=False (mapped onto a
    # continuous aggregate view created directly by migration 0020's raw
    # SQL), so Django issues no DDL for these CreateModel operations — this
    # just teaches makemigrations/the ORM about a schema it doesn't own.

    dependencies = [
        ('changesets', '0020_continuous_aggregates'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggVolumeHourly',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_volume_hourly',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggEditorDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_editor_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggImageryDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_imagery_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggLocaleDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_locale_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggContributorDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_contributor_daily',
                'managed': False,
            },
        ),
    ]
