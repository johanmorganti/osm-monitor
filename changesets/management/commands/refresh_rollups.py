import time
from django.core.management.base import BaseCommand
from changesets.rollups import refresh_rollups


class Command(BaseCommand):
    help = 'Rebuild the DailyVolume/DailyBreakdown rollup tables from the Changeset table.'

    def handle(self, *args, **options):
        start = time.monotonic()
        refresh_rollups()
        self.stdout.write(self.style.SUCCESS(f'Rollups refreshed in {time.monotonic() - start:.2f}s'))
