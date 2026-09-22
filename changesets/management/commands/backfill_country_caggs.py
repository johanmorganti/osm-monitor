import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import OperationalError

from changesets.cagg_maintenance import refresh_caggs_over_range

# The 5 CAggs added for the country dimension (migrations 0045/0048),
# created WITH NO DATA — their own 7-day refresh policy only materializes
# going forward from whenever it first fires, so without this one-time
# backfill they'd sit empty until the policy slowly caught up on its own.
# Separate from backfill_dimension_pair_caggs (which only covers pairs)
# since cagg_country_daily/cagg_country_hourly are single-dimension, not
# pairs — grouping all 5 country-related CAggs in one command instead.
COUNTRY_CAGG_NAMES = [
    'cagg_country_daily', 'cagg_country_hourly',
    'cagg_contributor_country_daily', 'cagg_country_editor_daily', 'cagg_country_imagery_daily',
]

# Matches the existing CAggs' own coverage start (see
# docs/todo/cagg-history-coverage-gap.md for why the single-dimension ones
# stop there rather than covering full 2005+ history).
DEFAULT_START = datetime(2025, 8, 1, tzinfo=dt_timezone.utc)


class Command(BaseCommand):
    help = (
        "One-time backfill for the 5 country CAggs added in migrations 0045/0048 "
        "(COUNTRY_CAGG_NAMES above). Idempotent — safe to re-run or interrupt and resume "
        "(refresh_continuous_aggregate over an already-current range is a cheap no-op). "
        "Auto-resuming as of 2026-09-22: this backfill crashed Postgres mid-run repeatedly (see "
        "docs/todo/continuous-aggregates-migration.md) — even with max_parallel_workers_per_"
        "gather=0 now a role default and small/paced batches, sustained back-to-back CALLs for "
        "roughly 20-45 minutes exhausted this host's headroom regardless of which specific CAgg "
        "was in flight or how the work was paced. This command walks the range one batch at a "
        "time *itself* (not via refresh_caggs_over_range's own internal loop) specifically so it "
        "can track the resume point as plain Python state, advanced only after a batch actually "
        "commits — the first version of this command instead re-derived the resume point from "
        "each CAgg's own max(bucket) after a crash, which silently produced a false-positive "
        "'complete' the very first time it was run: every CAgg's own automatic 7-day refresh "
        "policy keeps materializing the last few days regardless of this backfill's progress, so "
        "max(bucket) reads as 'basically now' even while a large gap sits earlier in the range — "
        "it answers 'what's the latest bucket', never 'is everything before it actually filled'."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date', type=str, default=None,
            help='YYYY-MM-DD to start from (default: 2025-08-01, matching the existing CAggs).'
        )
        parser.add_argument(
            '--batch-days', type=int, default=2,
            help='Days per refresh call, per CAgg (default: 2 — kept small given this host'
                 's demonstrated fragility under sustained load).'
        )
        parser.add_argument(
            '--pause-seconds', type=int, default=15,
            help='Seconds to sleep between date-batches, to let the host recover under '
                 'sustained load (default: 15).'
        )
        parser.add_argument(
            '--max-retries', type=int, default=20,
            help='Max number of crash-and-resume cycles before giving up and re-raising '
                 '(default: 20 — this is the "leave it running unattended" mode, so generous).'
        )
        parser.add_argument(
            '--crash-cooldown-seconds', type=int, default=90,
            help='Seconds to wait after a crash before checking whether Postgres has finished '
                 'recovering and resuming (default: 90 — observed WAL redo after these crashes '
                 'has taken 15-25s; this adds real margin on top, not just enough to reconnect).'
        )

    def handle(self, *args, **options):
        overall_start = cursor_start = (
            datetime.strptime(options['start_date'], '%Y-%m-%d').replace(tzinfo=dt_timezone.utc)
            if options['start_date'] else DEFAULT_START
        )
        end = datetime.now(dt_timezone.utc)
        batch_days = options['batch_days']
        batch_delta = timedelta(days=batch_days)
        pause_seconds = options['pause_seconds']
        max_retries = options['max_retries']
        crash_cooldown = options['crash_cooldown_seconds']

        # For the progress line below — total width of the whole requested
        # range, fixed at start, vs. how much of it cursor_start has moved
        # through so far. Not a % of *work* (some batches are much more
        # expensive than others — see cagg_contributor_country_daily's
        # cardinality note above), just a legible "how far through the
        # calendar range" indicator for someone watching this run live.
        total_days = max((end - overall_start).days, 1)
        total_crashes = 0
        run_started = time.monotonic()

        attempt = 0
        while cursor_start < end:
            batch_end = min(cursor_start + batch_delta, end)
            try:
                # The app role defaults to a bounded statement_timeout (see
                # db/init/02-role-statement-timeout.sh) — a dense batch's
                # refresh can legitimately run past that, so opt out. Must
                # be re-issued on every attempt, not just once at startup:
                # a crash closes this connection, and the fresh reconnect
                # after that would otherwise silently pick the role default
                # back up (same reasoning as poll_sequences.py).
                connection.cursor().execute("SET statement_timeout = 0")
                # One single batch per call (start/end exactly batch_days
                # apart) — refresh_caggs_over_range's own internal walking
                # loop is unused here on purpose, so *this* loop's
                # cursor_start is the only source of truth for progress,
                # advanced only after the line below returns successfully.
                refresh_caggs_over_range(
                    cursor_start, batch_end, cagg_names=COUNTRY_CAGG_NAMES, batch_days=batch_days,
                    stdout=self.stdout,
                )
                cursor_start = batch_end
                attempt = 0  # a clean batch resets the crash counter — only *consecutive* crashes should exhaust retries

                done_days = (cursor_start - overall_start).days
                pct = min(100, round(100 * done_days / total_days))
                elapsed_min = round((time.monotonic() - run_started) / 60, 1)
                self.stdout.write(
                    f'Progress: {cursor_start.date()} / {overall_start.date()}..{end.date()} '
                    f'({pct}%, {elapsed_min}min elapsed, {total_crashes} crash(es) so far)'
                )

                if cursor_start < end and pause_seconds:
                    time.sleep(pause_seconds)
            except OperationalError as e:
                attempt += 1
                total_crashes += 1
                if attempt > max_retries:
                    self.stdout.write(self.style.ERROR(
                        f'Crashed again (attempt {attempt}) and exhausted --max-retries — giving up '
                        f'at {cursor_start.date()}. {e}'
                    ))
                    raise
                self.stdout.write(self.style.WARNING(
                    f'Crashed (attempt {attempt}/{max_retries}, {total_crashes} total this run) '
                    f'refreshing {cursor_start.date()}..{batch_end.date()}: {e} — cooling down '
                    f'{crash_cooldown}s before resuming from {cursor_start.date()} (not skipping ahead)...'
                ))
                connection.close()  # drop the stale connection so the next query reconnects fresh
                time.sleep(crash_cooldown)
                self._wait_for_db()
                self.stdout.write('Database reachable again — resuming.')

        total_min = round((time.monotonic() - run_started) / 60, 1)
        self.stdout.write(self.style.SUCCESS(
            f'Country CAgg backfill complete: {overall_start.date()}..{end.date()} in '
            f'{total_min}min, {total_crashes} crash(es) survived.'
        ))

    def _wait_for_db(self, timeout_seconds=300, poll_interval=5):
        """Blocks until a trivial query succeeds, or timeout_seconds elapses
        (in which case the next refresh attempt just hits the same
        OperationalError and this whole retry cycle repeats — not a fatal
        error itself, just a bounded extra wait rather than slamming a
        database that's still mid WAL-redo). Logs a line every ~30s while
        waiting — previously silent here, which looked indistinguishable
        from a hang to anyone watching the log live."""
        deadline = time.monotonic() + timeout_seconds
        last_log = 0
        while time.monotonic() < deadline:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                return
            except OperationalError:
                connection.close()
                if time.monotonic() - last_log >= 30:
                    self.stdout.write('Still waiting for the database to come back...')
                    last_log = time.monotonic()
                time.sleep(poll_interval)
