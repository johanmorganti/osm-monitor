from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: CaggGeoFineDaily has managed=False (mapped onto the
    # continuous aggregate created directly by migration 0027's raw SQL),
    # so Django issues no DDL for this CreateModel operation — same as
    # 0026's CreateModel for CaggGeoDaily.

    dependencies = [
        ('changesets', '0027_cagg_geo_fine_daily'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggGeoFineDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('grid_lat', models.FloatField(null=True)),
                ('grid_lon', models.FloatField(null=True)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_geo_fine_daily',
                'managed': False,
            },
        ),
    ]
