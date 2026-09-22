from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: every model here has managed=False (mapped onto a
    # continuous aggregate view created directly by migration 0043's raw
    # SQL), so Django issues no DDL for these CreateModel operations — same
    # pattern as 0021/0042 for the other CAggs.

    dependencies = [
        ('changesets', '0043_cagg_contributor_pairs'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggContributorEditorDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('contributor', models.CharField(max_length=255)),
                ('editor', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_contributor_editor_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggContributorImageryDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('contributor', models.CharField(max_length=255)),
                ('imagery', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_contributor_imagery_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CaggContributorLocaleDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('contributor', models.CharField(max_length=255)),
                ('locale', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_contributor_locale_daily',
                'managed': False,
            },
        ),
    ]
