from django.db import migrations, models


class Migration(migrations.Migration):
    # A plain (non-hypertable) singleton table with one row — no need for
    # the atomic=False/RunSQL treatment the changesets_changeset migrations
    # need; Django's normal AddField is fine here.

    dependencies = [
        ('changesets', '0034_add_geo_hashed_cagg_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='rollupstate',
            name='last_created_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
