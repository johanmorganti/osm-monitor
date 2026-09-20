from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: managed=False, mapped onto migration 0033's continuous
    # aggregate — Django issues no DDL for this, same as 0021/0024/0026/0028.

    dependencies = [
        ('changesets', '0033_cagg_geo_hashed_daily'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggGeoHashedDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('geohash', models.CharField(max_length=12)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_geo_hashed_daily',
                'managed': False,
            },
        ),
    ]
