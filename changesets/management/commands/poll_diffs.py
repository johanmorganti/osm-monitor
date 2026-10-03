"""Follows OSM's minutely replication diffs and writes every object version
to ClickHouse (changesets/ingest/objects.py), backfilling the last
OBJECT_VERSIONS_DAYS from the daily diffs. See docs/decisions/object-changes.md.
"""
import gzip
import logging
import os
import tempfile
import time
from datetime import datetime, timezone

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from changesets.ingest.objects import OBJECT_VERSIONS_DAYS, DiffWriter
from changesets.models import DiffState

logger = logging.getLogger(__name__)

REPLICATION_URL = os.environ.get('OSM_REPLICATION_URL', 'https://planet.osm.org/replication')


def _path(sequence):
    digits = f'{sequence:09d}'
    return f'{digits[0:3]}/{digits[3:6]}/{digits[6:9]}'


class Replication:
    """minute/ and day/ diffs over one HTTP session: planet.osm.org redirects
    every file to its S3 mirror, and reusing the connections cut a file from
    ~2.6 s to ~0.5 s (measured 2026-10-03)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'osm-monitor (https://github.com/johanmorganti/osm-monitor)'

    def state(self, kind, sequence=None):
        """(sequence, timestamp) of the latest diff, or of `sequence`."""
        name = 'state.txt' if sequence is None else f'{_path(sequence)}.state.txt'
        response = self.session.get(f'{REPLICATION_URL}/{kind}/{name}', timeout=30)
        response.raise_for_status()
        values = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line and not line.startswith('#'))
        timestamp = datetime.strptime(values['timestamp'].replace('\\:', ':'), '%Y-%m-%dT%H:%M:%SZ')
        return int(values['sequenceNumber']), timestamp.replace(tzinfo=timezone.utc)

    def download(self, kind, sequence, fileobj):
        with self.session.get(f'{REPLICATION_URL}/{kind}/{_path(sequence)}.osc.gz', timeout=120, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(1 << 20):
                fileobj.write(chunk)
        fileobj.seek(0)

    def first_minute_after(self, moment, latest):
        """The first minutely sequence stamped after `moment` (binary search
        on the per-sequence state files; sequences are roughly one a minute
        but have gaps)."""
        lo = max(1, latest - int((datetime.now(timezone.utc) - moment).total_seconds() / 60 * 1.5) - 60)
        hi = latest
        while lo < hi:
            mid = (lo + hi) // 2
            if self.state('minute', mid)[1] > moment:
                hi = mid
            else:
                lo = mid + 1
        return lo


class Command(BaseCommand):
    help = (
        'Follow the minutely replication diffs into the ClickHouse object tables (live first), '
        f'and backfill the last {OBJECT_VERSIONS_DAYS} days from the daily diffs behind them.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=60,
                            help='Seconds to sleep when up to date (default: 60)')
        parser.add_argument('--backfill-days', type=int, default=OBJECT_VERSIONS_DAYS,
                            help=f'Days of daily diffs to backfill on first run (default: {OBJECT_VERSIONS_DAYS})')
        parser.add_argument('--batch-files', type=int, default=60,
                            help='Minutely diffs written per insert while catching up (default: 60)')
        parser.add_argument('--once', action='store_true',
                            help='Do one round (live catch-up, then one backfill day) and exit')

    def handle(self, *args, **options):
        replication = Replication()
        logger.info("Starting diff poller")
        while True:
            try:
                # Long writes (a daily diff takes minutes); re-issued every
                # round because the error path below reconnects.
                connection.cursor().execute("SET statement_timeout = 0")
                state = DiffState.objects.first() or self._init_state(replication, options['backfill_days'])
                did_work = self._live(replication, state, options['batch_files'])
                did_work = self._backfill_one_day(replication, state) or did_work
                if options['once']:
                    return
                if not did_work:
                    time.sleep(options['interval'])
            except CommandError:
                raise
            except Exception:
                logger.exception("Error in diff poll loop")
                connection.close()
                if options['once']:
                    raise
                time.sleep(options['interval'])

    def _init_state(self, replication, backfill_days):
        """Live starts at the first minute after the latest daily diff; the
        backfill covers that day and the ones before it."""
        latest_minute, _ = replication.state('minute')
        latest_day, midnight = replication.state('day')
        first_minute = replication.first_minute_after(midnight, latest_minute)
        state = DiffState.objects.create(
            last_minute=first_minute - 1,
            backfill_day=latest_day if backfill_days > 0 else None,
            backfill_floor=latest_day - backfill_days + 1 if backfill_days > 0 else None,
        )
        logger.info("First run", extra={
            'osm.diff.first_minute': first_minute, 'osm.diff.backfill_day': state.backfill_day,
            'osm.diff.backfill_floor': state.backfill_floor, 'osm.diff.day_end': midnight.isoformat(),
        })
        return state

    def _live(self, replication, state, batch_files):
        latest, _ = replication.state('minute')
        if latest <= state.last_minute:
            return False
        start = state.last_minute + 1
        while state.last_minute < latest:
            batch_start = time.monotonic()
            writer = DiffWriter()
            last = min(latest, state.last_minute + batch_files)
            for sequence in range(state.last_minute + 1, last + 1):
                with tempfile.TemporaryFile() as raw:
                    replication.download('minute', sequence, raw)
                    writer.add_file(gzip.GzipFile(fileobj=raw), 'minute', sequence)
            versions, changesets = writer.write()
            first = state.last_minute + 1
            state.last_minute = last
            state.save(update_fields=['last_minute', 'updated_at'])
            logger.info("Minutely diffs written", extra={
                'osm.diff.range_start': first, 'osm.diff.range_end': last, 'osm.diff.versions': versions,
                'osm.diff.changesets': changesets, 'osm.diff.seconds': round(time.monotonic() - batch_start, 1),
                'osm.diff.behind': latest - last,
            })
        logger.debug("Live caught up", extra={'osm.diff.range_start': start, 'osm.diff.range_end': latest})
        return True

    def _backfill_one_day(self, replication, state):
        if state.backfill_floor is None:
            return False
        if state.backfill_day < state.backfill_floor:
            logger.info("Backfill complete", extra={'osm.diff.backfill_floor': state.backfill_floor})
            state.backfill_day = state.backfill_floor = None
            state.save(update_fields=['backfill_day', 'backfill_floor', 'updated_at'])
            return False
        day = state.backfill_day
        started = time.monotonic()
        writer = DiffWriter()
        with tempfile.TemporaryFile() as raw:
            replication.download('day', day, raw)
            downloaded = time.monotonic() - started
            writer.add_file(gzip.GzipFile(fileobj=raw), 'day', day)
        versions, changesets = writer.write()
        state.backfill_day = day - 1
        state.save(update_fields=['backfill_day', 'updated_at'])
        logger.info("Daily diff written", extra={
            'osm.diff.day': day, 'osm.diff.versions': versions, 'osm.diff.changesets': changesets,
            'osm.diff.download_seconds': round(downloaded, 1),
            'osm.diff.seconds': round(time.monotonic() - started, 1),
            'osm.diff.backfill_remaining': day - state.backfill_floor,
        })
        return True
