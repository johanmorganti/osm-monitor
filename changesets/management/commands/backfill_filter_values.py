import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)

_BACKFILL_SQL = """
INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'contributor', "user" FROM changesets_changeset
WHERE "user" IS NOT NULL
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'editor', created_by_family FROM changesets_changeset
WHERE created_by_family IS NOT NULL
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'imagery', imagery_family FROM changesets_changeset
WHERE imagery_family IS NOT NULL
ON CONFLICT (field, value) DO NOTHING;
"""


class Command(BaseCommand):
    help = (
        'One-time backfill of FilterValue from all existing Changeset rows '
        '(refresh_rollups_incremental only populates it from rows inserted after '
        "FilterValue existed). Safe to re-run anytime — ON CONFLICT DO NOTHING "
        'makes it idempotent, e.g. after a bulk import that bypassed the poller.'
    )

    def handle(self, *args, **options):
        self.stdout.write('Backfilling FilterValue from all existing changesets — this scans the whole table, expect it to take a while...')
        start = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute(_BACKFILL_SQL)
            cursor.execute("SELECT field, COUNT(*) FROM changesets_filtervalue GROUP BY field ORDER BY field")
            counts = dict(cursor.fetchall())
        elapsed = time.monotonic() - start
        self.stdout.write(self.style.SUCCESS(
            f'Done in {elapsed:.1f}s. FilterValue rows: {counts}'
        ))
