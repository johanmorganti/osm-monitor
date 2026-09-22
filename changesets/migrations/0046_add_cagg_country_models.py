from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: CaggCountryDaily/CaggCountryHourly have managed=False
    # (mapped onto continuous aggregate views created directly by migration
    # 0045's raw SQL), so Django issues no DDL for these CreateModel
    # operations — same pattern as 0021 for the other single-dimension CAggs.

    dependencies = [
        ('changesets', '0045_cagg_country'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggCountryDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_country_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggCountryHourly',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_country_hourly',
                'managed': False,
            },
        ),
    ]
