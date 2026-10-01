from django.db import migrations

from changesets.geo import BBOX_DIAG_THRESHOLD_KM, GEOHASH_PRECISION


# geohash and country_code now come from the ingest parser
# (changesets/ingest/locate.py), so every analytics backend gets identical
# values without needing PostGIS. The trigger only keeps deriving `centroid`,
# the PostGIS geometry the Timescale backend's raw map queries use for their
# GiST-indexed viewport filter. The parser alone decides whether a bbox is
# trustworthy: no geohash means no centroid, so the two can't disagree.
#
# Parity before switching (3.17M rows over 400 random days, 2005-2026):
# geohash identical on every row, country different on 13 (0.0004%), all
# at the 5km nearest-country cutoff or between two near-equidistant
# neighbors, where PostGIS's geodesic polygon edges and the GeoJSON's
# straight lon/lat edges diverge slightly. Existing rows keep their values.
_REPLACE_TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION changeset_centroid_trigger() RETURNS trigger AS $$
BEGIN
    IF NEW.geohash IS NULL THEN
        NEW.centroid := NULL;
    ELSE
        NEW.centroid := ST_SetSRID(ST_MakePoint((NEW.min_lon + NEW.max_lon) / 2.0, (NEW.min_lat + NEW.max_lat) / 2.0), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Migration 0055's function body.
_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL = f"""
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


class Migration(migrations.Migration):

    dependencies = [
        ('changesets', '0056_filtervalue_language'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_REPLACE_TRIGGER_FUNCTION_SQL,
            reverse_sql=_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL,
        ),
    ]
