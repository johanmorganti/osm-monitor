from django.db import migrations

from changesets.geo import BBOX_DIAG_THRESHOLD_KM, GEOHASH_PRECISION


# Makes the centroid trigger's country lookup cheap enough for bulk imports.
# Measured on real centroids before this change: the 5km nearest-country
# fallback (migration 0053) cost ~135ms per point — `geom::geography` can't
# use the GiST index, so every miss (~2% of rows) computed a geodesic
# distance to all ~238 country polygons — which capped a full-history
# import_from_dump at ~260 changesets/s (~8 days for full history).
#
# Two changes, same results:
# - Lookups go through country_boundaries_subdivided: the same polygons cut
#   into small pieces (ST_Subdivide, <=128 vertices each). The index then
#   narrows candidates to a few small pieces instead of whole countries with
#   tens of thousands of vertices, and distance-to-country is still exact
#   (the minimum over its pieces). ~10x faster ST_Contains, measured.
# - The fallback prefilters with an index-usable bbox (`&&` on a ~5km
#   ST_Expand box, widened in longitude by 1/cos(lat)) before the exact
#   geography ST_DWithin/ordering. Together: ~0.25ms per miss, measured, with
#   identical results on the sample compared.
#
# ORDER BY iso_a2 on the ST_Contains lookup: a handful of points fall where
# two simplified polygons overlap (e.g. the RW/UG border), and LIMIT 1 alone
# picked one arbitrarily — now it's at least deterministic.
#
# country_boundaries stays the source of truth; load_country_boundaries
# rebuilds the subdivided table from it in the same transaction.

_CREATE_TABLE_SQL = """
CREATE TABLE country_boundaries_subdivided (
    id SERIAL PRIMARY KEY,
    iso_a2 CHAR(2) NOT NULL,
    geom geometry(Geometry, 4326) NOT NULL
);
CREATE INDEX country_boundaries_subdivided_geom_gist ON country_boundaries_subdivided USING GIST (geom);
INSERT INTO country_boundaries_subdivided (iso_a2, geom)
    SELECT iso_a2, ST_Subdivide(geom, 128) FROM country_boundaries;
ANALYZE country_boundaries_subdivided;
"""

_DROP_TABLE_SQL = "DROP TABLE country_boundaries_subdivided;"

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
        FROM country_boundaries_subdivided
        WHERE ST_Contains(geom, NEW.centroid)
        ORDER BY iso_a2
        LIMIT 1;

        IF NEW.country_code IS NULL THEN
            SELECT iso_a2 INTO NEW.country_code
            FROM country_boundaries_subdivided
            WHERE geom && ST_Expand(
                    NEW.centroid,
                    0.05 / greatest(cos(radians(ST_Y(NEW.centroid))), 0.02),
                    0.05
                  )
              AND ST_DWithin(geom::geography, NEW.centroid::geography, 5000)
            ORDER BY geom::geography <-> NEW.centroid::geography
            LIMIT 1;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Migration 0053's function body.
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

    IF NEW.centroid IS NULL THEN
        NEW.geohash := NULL;
        NEW.country_code := NULL;
    ELSE
        NEW.geohash := ST_GeoHash(NEW.centroid, {GEOHASH_PRECISION});
        SELECT iso_a2 INTO NEW.country_code
        FROM country_boundaries
        WHERE ST_Contains(geom, NEW.centroid)
        LIMIT 1;

        IF NEW.country_code IS NULL THEN
            SELECT iso_a2 INTO NEW.country_code
            FROM country_boundaries
            WHERE ST_DWithin(geom::geography, NEW.centroid::geography, 5000)
            ORDER BY geom::geography <-> NEW.centroid::geography
            LIMIT 1;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('changesets', '0053_country_code_nearest_fallback'),
    ]

    operations = [
        migrations.RunSQL(sql=_CREATE_TABLE_SQL, reverse_sql=_DROP_TABLE_SQL),
        migrations.RunSQL(
            sql=_REPLACE_TRIGGER_FUNCTION_SQL,
            reverse_sql=_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL,
        ),
    ]
