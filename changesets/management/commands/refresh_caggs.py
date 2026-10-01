import time
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import connection

from changesets.ingest.writers import get_writer


class Command(BaseCommand):
    help = (
        'Refresh every CAgg (stats + geo) and FilterValue over [start, end] — the same '
        'post-import pass import_from_dump runs at its end. Use it once after several '
        'import_from_dump --byte-range --skip-cagg-refresh workers have finished, or after '
        'any other bulk write outside the CAgg policies\' 7-day refresh window.'
    )

    def add_arguments(self, parser):
        parser.add_argument('start', type=date.fromisoformat, help='First day to refresh (YYYY-MM-DD)')
        parser.add_argument('end', type=date.fromisoformat, help='Last day to refresh, inclusive (YYYY-MM-DD)')

    def handle(self, *args, **options):
        # Long-running by design — opt out of the role's bounded default
        # (db/init/02-role-statement-timeout.sh).
        connection.cursor().execute("SET statement_timeout = 0")

        start = datetime.combine(options['start'], datetime.min.time(), tzinfo=dt_timezone.utc)
        end = datetime.combine(options['end'], datetime.min.time(), tzinfo=dt_timezone.utc) + timedelta(days=1)

        t0 = time.monotonic()
        get_writer('timescale').after_backfill(start, end, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(f'Done in {time.monotonic() - t0:.0f}s'))
