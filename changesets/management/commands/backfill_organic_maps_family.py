import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Min

from changesets.cagg_maintenance import refresh_caggs_over_range
from changesets.models import CaggEditorDaily, FilterValue

# `_parse_changeset_element` (osm_fetcher.py) had `family == 'Organic Maps'`
# — a comparison, not an assignment — so the Organic Maps remap never
# happened and every Organic Maps changeset was filed under the truncated
# `Organic` (whatever `split(' ')[0]` of `created_by` produced). Fixed
# 2026-09-18; this backfills rows imported before that fix. See
# docs/todo/editor-family-organic-maps-fix.md for the verification that
# every row under family `Organic` is a genuine Organic Maps string (no
# false positives — a blanket remap is safe) and the ~209,579-row scope.
#
# Newest-first, day-batched — same chunk-by-chunk discipline as every other
# backfill in this project (see CLAUDE.md's "Geo storage" section for why
# one statement over the whole table isn't done instead) — and idempotent
# via `WHERE created_by_family = 'Organic'`: safe to re-run or resume.
_BACKFILL_SQL = """
UPDATE changesets_changeset
SET created_by_family = 'Organic Maps'
WHERE created_at >= %(start)s AND created_at < %(end)s
  AND created_by_family = 'Organic';
"""


class Command(BaseCommand):
    help = (
        "Backfill created_by_family='Organic' -> 'Organic Maps' for existing rows "
        "(the code fix landed 2026-09-18; this catches up already-imported data), "
        "then refreshes cagg_editor_daily/cagg_editor_hourly over the affected range. "
        "Idempotent — safe to re-run or interrupt and resume. "
        "See docs/todo/editor-family-organic-maps-fix.md."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date', type=str, default=None,
            help='YYYY-MM-DD to start the newest-first walk from (default: now).'
        )
        parser.add_argument(
            '--batch-days', type=int, default=30,
            help='Days per UPDATE transaction, newest-first (default: 30).'
        )
        parser.add_argument(
            '--skip-cagg-refresh', action='store_true',
            help='Skip the CAgg refresh pass at the end (e.g. to run it separately/later).'
        )

    def handle(self, *args, **options):
        # The app role defaults to a bounded statement_timeout (see
        # db/init/02-role-statement-timeout.sh) — a dense month's UPDATE can
        # legitimately run past that, so opt out.
        connection.cursor().execute("SET statement_timeout = 0")

        start_date = options['start_date']
        batch_days = options['batch_days']
        batch_delta = timedelta(days=batch_days)

        # min(created_at) on the raw table for this filter has no chunk
        # exclusion to lean on (created_by_family isn't the partitioning
        # column) and has to visit every matching row across every chunk to
        # find the true minimum — measured at 10+ minutes and counting
        # before being cancelled. cagg_editor_daily already has exactly
        # this per-day breakdown precomputed and is tiny, so the same
        # lookup is instant there instead.
        earliest = CaggEditorDaily.objects.filter(name='Organic').aggregate(Min('bucket'))['bucket__min']
        if earliest is None:
            self.stdout.write('No rows with created_by_family=Organic — nothing to backfill.')
        else:
            cursor_end = (
                (datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=dt_timezone.utc) + timedelta(days=1))
                if start_date else datetime.now(dt_timezone.utc)
            )
            batch_start_cursor = cursor_end.replace(hour=0, minute=0, second=0, microsecond=0)
            floor = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
            earliest_touched = None
            latest_touched = None

            while batch_start_cursor > floor - batch_delta:
                batch_start = batch_start_cursor
                batch_end = batch_start + batch_delta

                self.stdout.write(f'{batch_start.date()}..{batch_end.date()}: starting')

                with transaction.atomic():
                    with connection.cursor() as cursor:
                        # Same reasoning as backfill_geohash_and_country.py:
                        # at least one chunk in range is already compressed
                        # (see docs/todo/compression-backlog.md), and a DML
                        # UPDATE can't decompress more than
                        # max_tuples_decompressed_per_dml_transaction tuples
                        # per transaction by default.
                        cursor.execute("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0")
                        start = time.monotonic()
                        cursor.execute(_BACKFILL_SQL, {'start': batch_start, 'end': batch_end})
                        updated = cursor.rowcount
                        elapsed = time.monotonic() - start

                if updated:
                    self.stdout.write(f'{batch_start.date()}..{batch_end.date()}: updated {updated} rows in {elapsed:.1f}s')
                    latest_touched = latest_touched or batch_end
                    earliest_touched = batch_start

                batch_start_cursor = batch_start - batch_delta

            self.stdout.write(self.style.SUCCESS('Row backfill complete — reached the earliest Organic row.'))

            fv_qs = FilterValue.objects.filter(field='editor', value='Organic')
            deleted, _ = fv_qs.delete()
            if deleted:
                self.stdout.write(self.style.SUCCESS("Deleted stale FilterValue('editor', 'Organic') row."))

            if options['skip_cagg_refresh']:
                self.stdout.write('Skipping CAgg refresh (--skip-cagg-refresh).')
            else:
                # Always refresh the *full* known range, not just whatever
                # sub-range this particular invocation happened to touch.
                # earliest_touched/latest_touched only get set when a batch's
                # UPDATE actually changes rows — a resumed run after every row
                # is already fixed finds nothing to update (both stay None)
                # even when a prior crash left the CAgg refresh itself
                # incomplete, which would silently skip it forever otherwise
                # (confirmed live 2026-09-20: a crash partway through the
                # refresh left cagg_editor_daily missing ~7 months of
                # 'Organic Maps' data, and the next resume found zero rows to
                # update and exited without noticing). CAgg refresh over an
                # already-current range is a cheap no-op, so there's no real
                # cost to always covering [earliest, now) instead of trying
                # to track exactly what's left refreshed.
                refresh_start = earliest_touched or earliest
                refresh_end = latest_touched or datetime.now(dt_timezone.utc)
                self.stdout.write(f'Refreshing cagg_editor_daily/cagg_editor_hourly over {refresh_start.date()}..{refresh_end.date()}...')
                refresh_caggs_over_range(
                    refresh_start, refresh_end,
                    cagg_names=['cagg_editor_daily', 'cagg_editor_hourly'],
                    stdout=self.stdout,
                )
                self.stdout.write(self.style.SUCCESS('CAgg refresh complete.'))
