import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import OperationalError

# One-time backfill for existing rows that predate migration 0053's
# nearest-within-5km fallback (only new/updated rows get it automatically,
# via the trigger). Naturally idempotent — the WHERE clause only ever
# matches rows still missing a country, so a crash or a plain re-run just
# picks up wherever it left off with no separate watermark needed, unlike
# the CAgg backfills (see backfill_country_caggs.py) which needed one.
_BACKFILL_SQL = """
UPDATE changesets_changeset
SET country_code = (
    SELECT iso_a2 FROM country_boundaries
    WHERE ST_DWithin(geom::geography, centroid::geography, 5000)
    ORDER BY geom::geography <-> centroid::geography
    LIMIT 1
)
WHERE centroid IS NOT NULL AND country_code IS NULL
  AND created_at >= %(start)s AND created_at < %(end)s;
"""

# Real earliest data (confirmed via `SELECT min(created_at)`) — much
# earlier than the single-dimension CAggs' own 2025-08-01 coverage start
# (see docs/todo/cagg-history-coverage-gap.md), since this table already
# holds full 2005+ history (see CLAUDE.md's "Design for full history"
# section) even though the CAggs themselves don't cover all of it yet.
DEFAULT_START = datetime(2005, 10, 1, tzinfo=dt_timezone.utc)


class Command(BaseCommand):
    help = (
        "One-time backfill: for existing rows with a centroid but no country_code (the gap "
        "migration 0053's nearest-within-5km fallback fixes going forward), apply that same "
        "fallback now. Idempotent — safe to re-run or interrupt and resume, no watermark needed "
        "(see _BACKFILL_SQL's own WHERE clause)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date', type=str, default=None,
            help='YYYY-MM-DD to start from (default: 2005-10-01, the table\'s actual earliest data).'
        )
        parser.add_argument(
            '--batch-days', type=int, default=30,
            help='Days per UPDATE batch (default: 30). Correlated subquery against a ~238-row '
                 'table per matching row — cheap regardless of chunk size — so this can be '
                 'coarser than the CAgg backfills\' 2-day batches.'
        )
        parser.add_argument(
            '--pause-seconds', type=int, default=3,
            help='Seconds to sleep between batches, for host courtesy (default: 3).'
        )

    def handle(self, *args, **options):
        cursor_start = (
            datetime.strptime(options['start_date'], '%Y-%m-%d').replace(tzinfo=dt_timezone.utc)
            if options['start_date'] else DEFAULT_START
        )
        end = datetime.now(dt_timezone.utc)
        batch_delta = timedelta(days=options['batch_days'])
        pause_seconds = options['pause_seconds']

        total_updated = 0
        run_started = time.monotonic()

        while cursor_start < end:
            batch_end = min(cursor_start + batch_delta, end)
            try:
                connection.cursor().execute("SET statement_timeout = 0")
                with connection.cursor() as cursor:
                    cursor.execute(_BACKFILL_SQL, {'start': cursor_start, 'end': batch_end})
                    updated = cursor.rowcount
                total_updated += updated
                if updated:
                    self.stdout.write(f'{cursor_start.date()}..{batch_end.date()}: fixed {updated} row(s)')
                cursor_start = batch_end
                if cursor_start < end and pause_seconds:
                    time.sleep(pause_seconds)
            except OperationalError as e:
                self.stdout.write(self.style.WARNING(
                    f'DB error on {cursor_start.date()}..{batch_end.date()}: {e} — waiting 30s and retrying same batch'
                ))
                connection.close()
                time.sleep(30)

        elapsed_min = round((time.monotonic() - run_started) / 60, 1)
        self.stdout.write(self.style.SUCCESS(
            f'Country-code fallback backfill complete: {total_updated} row(s) fixed in {elapsed_min}min.'
        ))
