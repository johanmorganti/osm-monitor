from django.core.management.base import BaseCommand
from django.db import connection

# Seeds FilterValue('country', <iso_a2>) directly from country_boundaries
# rather than scanning changesets_changeset for DISTINCT country_code — the
# two would produce the same set in practice (country_code is only ever
# assigned by the centroid-assignment trigger doing a point-in-polygon
# lookup against this same table, see migration 0031/0032), but this way
# costs one ~238-row scan of a small reference table instead of an index
# scan across the whole (23M+ row and growing) hypertable, and includes
# every possible country up front rather than only ones a changeset has
# already landed in.
_SEED_SQL = """
INSERT INTO changesets_filtervalue (field, value)
SELECT 'country', TRIM(iso_a2) FROM country_boundaries
ON CONFLICT (field, value) DO NOTHING;
"""


class Command(BaseCommand):
    help = (
        "One-time seed of FilterValue('country', ...) rows from country_boundaries, so "
        "/api/autocomplete/?field=country works without waiting on the (nonexistent, see "
        "rollups.py) incremental country discovery job. Idempotent (ON CONFLICT DO NOTHING) — "
        "safe to re-run, e.g. after load_country_boundaries adds a new country."
    )

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(_SEED_SQL)
            self.stdout.write(self.style.SUCCESS(f'Seeded {cursor.rowcount} new country FilterValue row(s).'))
