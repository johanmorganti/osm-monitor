from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import connection

from changesets.cagg_maintenance import refresh_caggs_over_range

# The 6 cross-dimension CAggs added in migrations 0041 (editor/imagery/
# language pairs) and 0043 (contributor pairs), all created WITH NO DATA —
# their own 7-day refresh policy only materializes going forward from
# whenever it first fires, so without this one-time backfill they'd sit
# empty (and ToplistView/TimeseriesView would see zero results for them)
# until the policy slowly caught up on its own over the following days.
# Re-running this after adding a new pair CAgg to the list is safe and
# cheap — the ones already backfilled are a fast no-op (see
# refresh_caggs_over_range's docstring).
PAIR_CAGG_NAMES = [
    'cagg_editor_imagery_daily', 'cagg_editor_locale_daily', 'cagg_imagery_locale_daily',
    'cagg_contributor_editor_daily', 'cagg_contributor_imagery_daily', 'cagg_contributor_locale_daily',
]

# Matches the existing single-dimension CAggs' own coverage start (confirmed
# via min(bucket) on cagg_editor_daily/cagg_imagery_daily, 2026-09-21) —
# these are meant to serve the same "past year" window ToplistView/
# TimeseriesView already query, not full 2005+ history (see
# docs/todo/cagg-history-coverage-gap.md for why the single-dimension ones
# stop there too).
DEFAULT_START = datetime(2025, 8, 1, tzinfo=dt_timezone.utc)


class Command(BaseCommand):
    help = (
        "One-time backfill for the 6 cross-dimension CAggs added in migrations 0041/0043 "
        "(PAIR_CAGG_NAMES above). Idempotent — safe to re-run or interrupt and resume "
        "(refresh_continuous_aggregate over an already-current range is a cheap no-op). "
        "Batch size defaults smaller than the usual 30 days (see CLAUDE.md's "
        "statement_timeout notes on this host's I/O constraints) — pass --batch-days to "
        "widen it once this has been proven safe."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date', type=str, default=None,
            help='YYYY-MM-DD (default: 2025-08-01, matching the existing single-dimension CAggs).'
        )
        parser.add_argument(
            '--batch-days', type=int, default=7,
            help='Days per refresh call, per CAgg (default: 7).'
        )

    def handle(self, *args, **options):
        # The app role defaults to a bounded statement_timeout (see
        # db/init/02-role-statement-timeout.sh) — a dense month's refresh
        # can legitimately run past that, so opt out (same as every other
        # backfill command touching changesets_changeset).
        connection.cursor().execute("SET statement_timeout = 0")

        start = (
            datetime.strptime(options['start_date'], '%Y-%m-%d').replace(tzinfo=dt_timezone.utc)
            if options['start_date'] else DEFAULT_START
        )
        end = datetime.now(dt_timezone.utc)
        batch_days = options['batch_days']

        self.stdout.write(
            f'Backfilling {", ".join(PAIR_CAGG_NAMES)} over {start.date()}..{end.date()} '
            f'in {batch_days}-day batches...'
        )
        refresh_caggs_over_range(start, end, cagg_names=PAIR_CAGG_NAMES, batch_days=batch_days, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS('Pair CAgg backfill complete.'))
