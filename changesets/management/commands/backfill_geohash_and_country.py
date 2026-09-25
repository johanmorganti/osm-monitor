import logging
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from changesets.geo import GEOHASH_PRECISION

logger = logging.getLogger(__name__)

# One UPDATE per batch, newest first — same "live/recent data matters most,
# older history fills in behind it" order poll_sequences.py's own backfill
# already uses, and the same chunk-by-chunk discipline every other backfill
# in this project follows (see CLAUDE.md's "Geo storage: a single geohash
# key" section) rather than one statement over the whole table.
#
# Batch size is configurable (--batch-days, default 30 ≈ the original
# fixed-monthly design) rather than hardcoded to calendar months — added
# 2026-09-18 after repeated Postgres crashes (signal 13, broken pipe) mid-
# transaction: a smaller batch means less WAL to redo on crash recovery and less committed
# work lost per crash, at the cost of more (cheap, idempotent no-op) round
# trips through already-backfilled ranges. Every batch is still its own
# transaction, so correctness/idempotency is identical regardless of size.
#
# geohash is set for every row with a non-NULL centroid regardless of
# country match (a scalar correlated subquery, not a FROM-join, so a
# non-matching centroid — open ocean, or one of the ~20 disputed/unmapped
# territories excluded from country_boundaries — still gets its geohash,
# just a NULL country_code, rather than being skipped by INNER JOIN
# semantics). LIMIT 1 matches the trigger function's own tie-breaking
# exactly (migration 0032) — a point that happens to fall in more than one
# simplified boundary near a shared border resolves the same way whether it
# arrived via this backfill or via a live insert.
#
# `WHERE geohash IS NULL` makes each batch idempotent: safe to re-run or
# resume after an interruption without redoing already-backfilled rows.
_BACKFILL_SQL = """
UPDATE changesets_changeset c
SET geohash = ST_GeoHash(c.centroid, %(precision)s),
    country_code = (
        SELECT cb.iso_a2 FROM country_boundaries cb
        WHERE ST_Contains(cb.geom, c.centroid)
        LIMIT 1
    )
WHERE c.created_at >= %(start)s AND c.created_at < %(end)s
  AND c.centroid IS NOT NULL
  AND c.geohash IS NULL;
"""


class Command(BaseCommand):
    help = (
        'Backfill geohash/country_code (migration 0032) for existing Changeset rows, '
        'in fixed-size day batches (--batch-days, default 30), newest first. '
        'Idempotent — safe to re-run or interrupt and resume. '
        "See CLAUDE.md's \"Geo storage: a single geohash key\" section."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-months', type=int, default=None,
            help='Stop after this many batches (kept the old flag name for compatibility; counts batches, not calendar months, when --batch-days != 30).'
        )
        parser.add_argument(
            '--start-date', type=str, default=None,
            help='YYYY-MM-DD to start the newest-first walk from (default: now).'
        )
        parser.add_argument(
            '--batch-days', type=int, default=30,
            help='Days per UPDATE transaction, newest-first (default: 30, ≈ one calendar month). '
                 'Smaller batches mean less WAL to redo and less work lost per crash, at the cost '
                 'of more round trips through already-backfilled ranges.'
        )

    def handle(self, *args, **options):
        # The app role defaults to a bounded statement_timeout (see
        # db/init/02-role-statement-timeout.sh) — a dense batch's UPDATE
        # (potentially 1M+ rows, each doing a point-in-polygon lookup) can
        # legitimately run past that, so opt out.
        connection.cursor().execute("SET statement_timeout = 0")

        max_months = options['max_months']
        start_date = options['start_date']
        batch_days = options['batch_days']
        batch_delta = timedelta(days=batch_days)

        with connection.cursor() as cursor:
            cursor.execute("SELECT min(created_at) FROM changesets_changeset WHERE centroid IS NOT NULL")
            (earliest,) = cursor.fetchone()
        if earliest is None:
            self.stdout.write('No rows with a centroid — nothing to backfill.')
            return

        cursor_end = (
            (datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=dt_timezone.utc) + timedelta(days=1))
            if start_date else datetime.now(dt_timezone.utc)
        )
        # Walk newest-first in fixed-size day batches, down past `earliest`.
        batch_start_cursor = cursor_end.replace(hour=0, minute=0, second=0, microsecond=0)
        floor = earliest.replace(hour=0, minute=0, second=0, microsecond=0)

        months_done = 0
        while batch_start_cursor > floor - batch_delta:
            batch_start = batch_start_cursor
            batch_end = batch_start + batch_delta

            # Printed before the UPDATE runs, not just after it finishes —
            # this line is what lets a log-file-only check (`docker compose
            # exec web tail ...`, no new Postgres connection at all) tell
            # "still working on this month" apart from "process died
            # silently", for a month whose UPDATE can run for over an hour.
            self.stdout.write(f'{batch_start.date()}..{batch_end.date()}: starting')

            # transaction.atomic() (not just a shared cursor) is what makes
            # the SET LOCAL below apply to the UPDATE that follows it —
            # Django's default autocommit mode would otherwise commit (and
            # so discard) it as its own transaction before the UPDATE ever
            # ran. Same pattern as backfill_imagery_family_none.py.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # See backfill_imagery_family_none.py for why this is
                    # needed: a DML UPDATE can't decompress more than
                    # max_tuples_decompressed_per_dml_transaction tuples per
                    # transaction by default, and at least one chunk is
                    # already compressed (see docs/todo/compression-backlog.md).
                    cursor.execute("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0")
                    start = time.monotonic()
                    cursor.execute(_BACKFILL_SQL, {
                        'start': batch_start, 'end': batch_end, 'precision': GEOHASH_PRECISION,
                    })
                    updated = cursor.rowcount
                    elapsed = time.monotonic() - start

            if updated:
                self.stdout.write(f'{batch_start.date()}..{batch_end.date()}: updated {updated} rows in {elapsed:.1f}s')
                logger.info(
                    "Geohash/country backfill batch",
                    extra={
                        'osm.backfill.range_start': str(batch_start.date()),
                        'osm.backfill.range_end': str(batch_end.date()),
                        'osm.backfill.rows_updated': updated,
                        'osm.backfill.seconds': round(elapsed, 1),
                    },
                )

            batch_start_cursor = batch_start - batch_delta
            months_done += 1
            if max_months is not None and months_done >= max_months:
                self.stdout.write(self.style.WARNING(f'Stopping after --max-months={max_months} (test batch).'))
                return

        self.stdout.write(self.style.SUCCESS('Backfill complete — reached the earliest batch with a centroid.'))
