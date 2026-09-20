from django.db import migrations

from changesets.geo import BBOX_DIAG_THRESHOLD_KM


# First real PostGIS adoption in this app (see TODO.md / CLAUDE.md for the
# timescaledb-ha migration this depends on) — a real geometry(Point, 4326)
# column, GiST-indexed, maintained by a trigger rather than application
# code. Replaces the hand-rolled equirectangular approximation in
# changesets/geo.py's BBOX_DIAG_KM_SQL with real geodesic ST_Distance for
# the bbox-quality gate, and — more importantly — gives GeoView's raw
# (filtered) fallback path something it never had: an index it can actually
# use for viewport bounds, instead of computing an ungated, unindexable
# grid expression per row on every request (see TODO.md's "filtered +
# fine-zoom" entry).
#
# Trigger, not a GENERATED STORED column: a GENERATED column would force
# Postgres to rewrite every existing row (all ~23M, across every chunk) as
# part of a single ALTER TABLE — exactly the kind of heavy, all-at-once
# table rewrite this project has been careful to avoid on this
# memory-constrained host all along. ADD COLUMN with no default is a fast,
# metadata-only operation instead; the trigger populates new/updated rows
# going forward, and a separate, careful, chunk-by-chunk backfill (same
# approach as every CAgg backfill so far) populates existing history.
#
# NULL semantics unchanged from the old equirectangular gate: missing bbox
# or bbox diagonal over BBOX_DIAG_THRESHOLD_KM -> NULL centroid, meaning
# "don't trust this changeset's location" — callers already filter NULL
# out, so this is a drop-in replacement, not a behavior change.
_BBOX_DIAG_METERS = BBOX_DIAG_THRESHOLD_KM * 1000

_ADD_COLUMN_SQL = "ALTER TABLE changesets_changeset ADD COLUMN centroid geometry(Point, 4326);"

_CREATE_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION changeset_centroid_trigger() RETURNS trigger AS $$
BEGIN
    IF NEW.min_lat IS NULL OR NEW.max_lat IS NULL OR NEW.min_lon IS NULL OR NEW.max_lon IS NULL THEN
        NEW.centroid := NULL;
    ELSIF ST_Distance(
        ST_SetSRID(ST_MakePoint(NEW.min_lon, NEW.min_lat), 4326)::geography,
        ST_SetSRID(ST_MakePoint(NEW.max_lon, NEW.max_lat), 4326)::geography
    ) > {_BBOX_DIAG_METERS} THEN
        NEW.centroid := NULL;
    ELSE
        NEW.centroid := ST_SetSRID(ST_MakePoint((NEW.min_lon + NEW.max_lon) / 2.0, (NEW.min_lat + NEW.max_lat) / 2.0), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_TRIGGER_SQL = """
CREATE TRIGGER changeset_centroid_trigger_insupd
BEFORE INSERT OR UPDATE OF min_lat, max_lat, min_lon, max_lon ON changesets_changeset
FOR EACH ROW EXECUTE FUNCTION changeset_centroid_trigger();
"""

_CREATE_INDEX_SQL = "CREATE INDEX changeset_centroid_gist ON changesets_changeset USING GIST (centroid);"

_DROP_TRIGGER_SQL = "DROP TRIGGER IF EXISTS changeset_centroid_trigger_insupd ON changesets_changeset;"
_DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS changeset_centroid_trigger();"
_DROP_INDEX_SQL = "DROP INDEX IF EXISTS changeset_centroid_gist;"
_DROP_COLUMN_SQL = "ALTER TABLE changesets_changeset DROP COLUMN IF EXISTS centroid;"


class Migration(migrations.Migration):
    # Index creation on a hypertable is a catalog operation propagated to
    # every chunk — kept out of one transaction, same reason as every other
    # RunSQL migration touching changesets_changeset (0015, 0018, 0019...).
    atomic = False

    dependencies = [
        ('changesets', '0028_add_geo_fine_cagg_model'),
    ]

    operations = [
        migrations.RunSQL(sql=_ADD_COLUMN_SQL, reverse_sql=_DROP_COLUMN_SQL),
        migrations.RunSQL(sql=_CREATE_TRIGGER_FUNCTION_SQL, reverse_sql=_DROP_FUNCTION_SQL),
        migrations.RunSQL(sql=_CREATE_TRIGGER_SQL, reverse_sql=_DROP_TRIGGER_SQL),
        migrations.RunSQL(sql=_CREATE_INDEX_SQL, reverse_sql=_DROP_INDEX_SQL),
    ]
