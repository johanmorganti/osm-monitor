from django.db import migrations


# Reference table for Changeset.country_code's point-in-polygon lookup (see
# the next migration) — world country polygons with ISO 3166-1 alpha-2
# codes. A normal (non-hypertable) table, small and static: 238 rows,
# loaded from changesets/data/country_boundaries.geojson via the
# load_country_boundaries management command, not by this migration —
# schema only here, same split as Changeset.centroid's own column-then-
# separate-backfill pattern (migration 0029).
#
# Deliberately allows multiple rows per iso_a2 (a country's mainland and its
# overseas territories/exclaves are often separate polygons in the source
# data) — a point-in-polygon lookup with LIMIT 1 works the same either way,
# and forcing a single merged multipolygon per country buys nothing here.
_CREATE_TABLE_SQL = """
CREATE TABLE country_boundaries (
    id SERIAL PRIMARY KEY,
    iso_a2 CHAR(2) NOT NULL,
    name TEXT NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
"""

_CREATE_INDEX_SQL = "CREATE INDEX country_boundaries_geom_gist ON country_boundaries USING GIST (geom);"

_DROP_TABLE_SQL = "DROP TABLE IF EXISTS country_boundaries;"


class Migration(migrations.Migration):
    dependencies = [
        ('changesets', '0030_drop_tags_column'),
    ]

    operations = [
        migrations.RunSQL(sql=_CREATE_TABLE_SQL, reverse_sql=_DROP_TABLE_SQL),
        migrations.RunSQL(sql=_CREATE_INDEX_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
