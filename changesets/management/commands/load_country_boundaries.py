import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection, transaction

# See changesets/data/README.md for provenance, license, and the ISO-code
# corrections/exclusions already applied to this file.
_DATA_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'country_boundaries.geojson'


class Command(BaseCommand):
    help = (
        'One-time load of changesets/data/country_boundaries.geojson into the '
        'country_boundaries table (schema created by migration 0031). Safe to '
        're-run — clears and reloads rather than appending duplicates.'
    )

    def handle(self, *args, **options):
        data = json.loads(_DATA_PATH.read_text())
        features = data['features']
        self.stdout.write(f'Loading {len(features)} country boundaries from {_DATA_PATH.name}...')

        # transaction.atomic() so a concurrent insert's centroid trigger
        # (migration 0032, does a live point-in-polygon lookup against this
        # same table) only ever sees the table fully populated or fully
        # pre-load — never truncated-and-partway-reloaded, which would
        # otherwise resolve country_code to NULL for anything ingested
        # during that narrow window.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('TRUNCATE country_boundaries RESTART IDENTITY')
                for feature in features:
                    props = feature['properties']
                    cursor.execute(
                        "INSERT INTO country_boundaries (iso_a2, name, geom) "
                        "VALUES (%s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))",
                        [props['iso_a2'], props['name'], json.dumps(feature['geometry'])],
                    )

        self.stdout.write(self.style.SUCCESS(f'Loaded {len(features)} country boundaries.'))
