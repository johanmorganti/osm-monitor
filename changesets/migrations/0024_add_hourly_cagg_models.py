from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: every model here has managed=False (mapped onto a
    # continuous aggregate view created directly by migration 0023's raw
    # SQL), so Django issues no DDL for these CreateModel operations — this
    # just teaches makemigrations/the ORM about a schema it doesn't own.

    dependencies = [
        ('changesets', '0023_hourly_and_daily_volume_caggs'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggVolumeDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_volume_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggEditorHourly',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_editor_hourly',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggImageryHourly',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_imagery_hourly',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggLocaleHourly',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_locale_hourly',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggContributorHourly',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_contributor_hourly',
                'managed': False,
            },
        ),
    ]
