from django.db import migrations

from changesets.geo import BBOX_DIAG_THRESHOLD_KM, GEOHASH_PRECISION


# Out-of-range bboxes get no centroid instead of failing the insert. A few
# early changesets in the planet dump carry coordinates like +/-214.748
# (2^31 / 1e7, an old integer-overflow artifact); ST_GeoHash raises on them,
# which failed the whole bulk insert and, in the per-row fallback, dropped
# that changeset entirely. Same outcome as an oversized bbox already has:
# stored as-is, just no centroid/geohash/country.

_REPLACE_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION changeset_centroid_trigger() RETURNS trigger AS $$
BEGIN
    IF NEW.min_lat IS NULL OR NEW.max_lat IS NULL OR NEW.min_lon IS NULL OR NEW.max_lon IS NULL THEN
        NEW.centroid := NULL;
    ELSIF abs(NEW.min_lon) > 180 OR abs(NEW.max_lon) > 180 OR abs(NEW.min_lat) > 90 OR abs(NEW.max_lat) > 90 THEN
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

# Migration 0054's function body.
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


class Migration(migrations.Migration):

    dependencies = [
        ('changesets', '0054_country_boundaries_subdivided'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_REPLACE_TRIGGER_FUNCTION_SQL,
            reverse_sql=_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL,
        ),
    ]
