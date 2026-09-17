from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: CaggGeoDaily has managed=False (mapped onto the continuous
    # aggregate created directly by migration 0025's raw SQL), so Django
    # issues no DDL for this CreateModel operation — this just teaches
    # makemigrations/the ORM about a schema it doesn't own, same as 0024's
    # CreateModel operations for the hourly CAggs.

    dependencies = [
        ('changesets', '0025_cagg_geo_daily'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggGeoDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('grid_lat', models.FloatField(null=True)),
                ('grid_lon', models.FloatField(null=True)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_geo_daily',
                'managed': False,
            },
        ),
    ]
