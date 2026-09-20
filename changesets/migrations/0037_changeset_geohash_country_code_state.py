from django.db import migrations, models


# State-only: migration 0032 added the `geohash`/`country_code` columns and
# their btree indexes (changeset_geohash_idx, changeset_country_code_idx)
# via plain RunSQL, without a matching AddField/AddIndex state operation —
# an oversight discovered 2026-09-19 when GeoView's geohash-based rewrite
# (docs/todo/geo-spatial-key-and-country.md) tried to query
# `Changeset.objects.filter(geohash=...)` through the ORM and Django raised
# FieldError: Cannot resolve keyword 'geohash' into field. The columns and
# indexes already exist in the database (migration 0032) and are correctly
# populated (the centroid trigger, and the full backfill completed this
# session) — this migration only teaches Django's ORM/migration state about
# schema it doesn't own yet, issuing no DDL of its own, same pattern as
# 0026/0028's CreateModel operations for the CAgg models.

class Migration(migrations.Migration):
    dependencies = [
        ('changesets', '0036_drop_like_pattern_indexes'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='changeset',
                    name='geohash',
                    field=models.CharField(max_length=12, null=True, blank=True),
                ),
                migrations.AddField(
                    model_name='changeset',
                    name='country_code',
                    field=models.CharField(max_length=2, null=True, blank=True),
                ),
                migrations.AddIndex(
                    model_name='changeset',
                    index=models.Index(fields=['geohash'], name='changeset_geohash_idx'),
                ),
                migrations.AddIndex(
                    model_name='changeset',
                    index=models.Index(fields=['country_code'], name='changeset_country_code_idx'),
                ),
            ],
            database_operations=[],
        ),
    ]
