from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: managed=False, mapped onto the continuous aggregate view
    # created directly by migration 0050's raw SQL — same pattern as
    # 0042/0044/0046/0049 for every other CAgg model.

    dependencies = [
        ('changesets', '0050_cagg_editor_version'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaggEditorVersionDaily',
            fields=[
                ('bucket', models.DateTimeField(primary_key=True, serialize=False)),
                ('editor', models.CharField(max_length=255)),
                ('version', models.CharField(max_length=255)),
                ('cnt', models.BigIntegerField()),
                ('changes_sum', models.BigIntegerField()),
            ],
            options={
                'db_table': 'cagg_editor_version_daily',
                'managed': False,
            },
        ),
    ]
