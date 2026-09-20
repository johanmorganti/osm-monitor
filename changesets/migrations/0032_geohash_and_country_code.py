from django.db import migrations

from changesets.geo import BBOX_DIAG_THRESHOLD_KM, GEOHASH_PRECISION


# Two new derived columns, both computed from the same centroid the
# existing trigger (migration 0029) already maintains — extending that one
# trigger function rather than adding two more, so all three derived
# columns (centroid, geohash, country_code) share one code path and can't
# drift out of sync with each other.
#
# geohash: see changesets/geo.py's "Geohash-based grid" section for why —
# replaces the old two-CAgg/two-column (grid_lat/grid_lon) geo design.
# country_code: ISO 3166-1 alpha-2, point-in-polygon against
# country_boundaries (migration 0031). NULL whenever centroid is NULL
# (untrustworthy bbox — same gate as before) *or* when centroid doesn't
# fall inside any loaded boundary (open ocean, or one of the ~20
# disputed/unmapped territories excluded from country_boundaries — see
# changesets/data/README.md). Both cases are real "unknown", not an error;
# callers must handle NULL as its own bucket rather than treating it as
# missing data to backfill away.
#
# ADD COLUMN with no default, same reasoning as 0029: fast metadata-only
# operation on a hypertable, no rewrite of existing rows. New/updated rows
# get both from the trigger going forward; existing rows need a separate,
# monitored, chunk-by-chunk backfill (see docs/todo/geo-spatial-key-and-
# country.md) — not part of this migration.
_ADD_COLUMNS_SQL = (
    "ALTER TABLE changesets_changeset "
    "ADD COLUMN geohash varchar(12), "
    f"ADD COLUMN country_code char(2);"
)

_REPLACE_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION changeset_centroid_trigger() RETURNS trigger AS $$
BEGIN
    IF NEW.min_lat IS NULL OR NEW.max_lat IS NULL OR NEW.min_lon IS NULL OR NEW.max_lon IS NULL THEN
        NEW.centroid := NULL;
    ELSIF ST_Distance(
        ST_SetSRID(ST_MakePoint(NEW.min_lon, NEW.min_lat), 4326)::geography,
        ST_SetSRID(ST_MakePoint(NEW.max_lon, NEW.max_lat), 4326)::geography
    ) > {BBOX_DIAG_THRESHOLD_KM * 1000} THEN
        NEW.centroid := NULL;
    ELSE
        NEW.centroid := ST_SetSRID(ST_MakePoint((NEW.min_lon + NEW.max_lon) / 2.0, (NEW.min_lat + NEW.max_lat) / 2.0), 4326);
    END IF;

    IF NEW.centroid IS NULL THEN
        NEW.geohash := NULL;
        NEW.country_code := NULL;
    ELSE
        NEW.geohash := ST_GeoHash(NEW.centroid, {GEOHASH_PRECISION});
        SELECT iso_a2 INTO NEW.country_code
        FROM country_boundaries
        WHERE ST_Contains(geom, NEW.centroid)
        LIMIT 1;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Reverts to migration 0029's original function body (no geohash/country_code).
_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION changeset_centroid_trigger() RETURNS trigger AS $$
BEGIN
    IF NEW.min_lat IS NULL OR NEW.max_lat IS NULL OR NEW.min_lon IS NULL OR NEW.max_lon IS NULL THEN
        NEW.centroid := NULL;
    ELSIF ST_Distance(
        ST_SetSRID(ST_MakePoint(NEW.min_lon, NEW.min_lat), 4326)::geography,
        ST_SetSRID(ST_MakePoint(NEW.max_lon, NEW.max_lat), 4326)::geography
    ) > {BBOX_DIAG_THRESHOLD_KM * 1000} THEN
        NEW.centroid := NULL;
    ELSE
        NEW.centroid := ST_SetSRID(ST_MakePoint((NEW.min_lon + NEW.max_lon) / 2.0, (NEW.min_lat + NEW.max_lat) / 2.0), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Plain btree on geohash: text sorts lexicographically the same way geohash
# prefixes nest, so a range condition (see geo.py's geohash_prefix_range_sql)
# is a normal sargable index-range scan — no pattern-ops/trigram index
# needed, same class of fix as this project's existing UPPER() expression
# indexes (avoid the wrong index shape rather than add a heavier one).
_CREATE_GEOHASH_INDEX_SQL = "CREATE INDEX changeset_geohash_idx ON changesets_changeset (geohash);"
_CREATE_COUNTRY_INDEX_SQL = "CREATE INDEX changeset_country_code_idx ON changesets_changeset (country_code);"

_DROP_COLUMNS_SQL = (
    "ALTER TABLE changesets_changeset "
    "DROP COLUMN IF EXISTS geohash, "
    "DROP COLUMN IF EXISTS country_code;"
)


class Migration(migrations.Migration):
    # Index creation on a hypertable propagates to every chunk's catalog
    # entry — same reasoning as every other RunSQL migration touching
    # changesets_changeset (0015, 0018, 0019, 0029...).
    atomic = False

    dependencies = [
        ('changesets', '0031_country_boundaries'),
    ]

    operations = [
        migrations.RunSQL(sql=_ADD_COLUMNS_SQL, reverse_sql=_DROP_COLUMNS_SQL),
        migrations.RunSQL(
            sql=_REPLACE_TRIGGER_FUNCTION_SQL,
            reverse_sql=_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL,
        ),
        migrations.RunSQL(sql=_CREATE_GEOHASH_INDEX_SQL, reverse_sql="DROP INDEX IF EXISTS changeset_geohash_idx;"),
        migrations.RunSQL(sql=_CREATE_COUNTRY_INDEX_SQL, reverse_sql="DROP INDEX IF EXISTS changeset_country_code_idx;"),
    ]
