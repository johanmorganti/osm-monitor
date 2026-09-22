from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: every model here has managed=False (mapped onto a
    # continuous aggregate view created directly by migration 0048's raw
    # SQL) — same pattern as 0042/0044 for the other pair CAggs.

    dependencies = [
        ('changesets', '0048_cagg_country_pairs'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggContributorCountryDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('contributor', models.CharField(max_length=255)),
                ('country', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_contributor_country_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggCountryEditorDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('country', models.CharField(max_length=255)),
                ('editor', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_country_editor_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggCountryImageryDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('country', models.CharField(max_length=255)),
                ('imagery', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_country_imagery_daily',
                'managed': False,
            },
        ),
    ]
