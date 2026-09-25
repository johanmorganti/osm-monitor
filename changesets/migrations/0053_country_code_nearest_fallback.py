from django.db import migrations

from changesets.geo import BBOX_DIAG_THRESHOLD_KM, GEOHASH_PRECISION


# Fixes a real gap found investigating why the dashboard's "(none)" country
# bucket was unexpectedly large (~4% of changesets, #7 in the Top 20 Countries
# toplist): country_boundaries' polygons were simplified with a 0.02°
# (~2.2km) tolerance for storage/render cost (see changesets/data/README.md),
# which is too coarse for exact ST_Contains membership testing — a sample of
# centroid-not-null-but-country_code-null rows found 94/100 within 5km of the
# correct country (avg 2.9km), only 1/100 genuinely remote. Since OSM editing
# skews toward populated, often coastal areas, this was systematically
# clipping just-inland/coastal edits out of their own country.
#
# Fix: when exact ST_Contains finds no match, fall back to the *nearest*
# country_boundaries polygon within 5km (not unconditionally nearest — a
# genuine open-ocean/Antarctica point should still end up NULL rather than
# being assigned to whatever coastline happens to be closest). 5km is chosen
# to comfortably cover the observed ~2.9km average miss with margin, while
# staying well short of "assign anything to the nearest coastline
# regardless." Geography-cast distance (real meters, not raw degrees) — this
# fallback only runs on already-failed lookups (~2.4% of rows going
# forward), and country_boundaries is a ~238-row table, so the extra cost
# here is negligible regardless of index shape.
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

# Reverts to migration 0032's function body (exact ST_Contains only, no
# nearest-within-5km fallback).
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
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    # CREATE OR REPLACE FUNCTION only — no column/index change, so no
    # reason to take this out of the default transaction the way the
    # column-adding migrations (0029/0032) had to.

    dependencies = [
        ('changesets', '0052_filtervalue_country_choice'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_REPLACE_TRIGGER_FUNCTION_SQL,
            reverse_sql=_RESTORE_PREVIOUS_TRIGGER_FUNCTION_SQL,
        ),
    ]
