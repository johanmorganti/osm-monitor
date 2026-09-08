import time
from django.core.management.base import BaseCommand
from changesets.rollups import refresh_rollups


class Command(BaseCommand):
    help = (
        'Full rebuild of the DailyVolume/DailyBreakdown rollup tables from the ENTIRE '
        'Changeset table. The poller no longer runs this automatically (see '
        'refresh_rollups_reconcile, which handles routine drift correction on a bounded '
        'recent window instead) — this is a manual escape hatch for when something '
        'broader needs correcting, e.g. after a bulk import or a data-fixing script that '
        'touched old rows. Expect it to be slow at scale.'
    )

    def handle(self, *args, **options):
        start = time.monotonic()
        refresh_rollups()
        self.stdout.write(self.style.SUCCESS(f'Rollups refreshed in {time.monotonic() - start:.2f}s'))
