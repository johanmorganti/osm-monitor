import logging
import time
import os
import requests
import yaml
from django.core.management.base import BaseCommand, CommandError
from changesets.models import SequenceState
from changesets.osm_fetcher import process_sequence
from changesets.rollups import refresh_rollups

logger = logging.getLogger(__name__)

PLANET_STATE_URL = 'https://planet.osm.org/replication/changesets/state.yaml'

# OSM changeset replication is minutely, so this is the default rate used to
# convert --backfill-days into a sequence count. It's an approximation — actual
# cadence can drift — but good enough to size the backfill window.
SEQUENCES_PER_DAY_DEFAULT = 1440


def fetch_latest_sequence():
    response = requests.get(PLANET_STATE_URL, stream=True, timeout=30)
    response.raise_for_status()
    data = yaml.safe_load(response.raw.read())
    return int(data['sequence'])


class Command(BaseCommand):
    help = (
        'Poll planet.osm.org for new changeset sequences and ingest them continuously. '
        'Live sequences are always processed first so the dashboard has current data, '
        'then older history is backfilled backward in time behind that point.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval', type=int, default=60,
            help='Seconds to sleep between polls when there is nothing to do (default: 60)'
        )
        parser.add_argument(
            '--start', type=int, default=None,
            help='Sequence number to start live polling from on first run (default: the current '
                 'latest sequence, i.e. start from now)'
        )
        parser.add_argument(
            '--backfill-days', type=int, default=365,
            help='How many days of history to backfill behind the live start point (default: 365)'
        )
        parser.add_argument(
            '--sequences-per-day', type=int, default=SEQUENCES_PER_DAY_DEFAULT,
            help=f'Sequences per day, used to size the backfill window (default: {SEQUENCES_PER_DAY_DEFAULT})'
        )
        parser.add_argument(
            '--backfill-batch-size', type=int, default=1000,
            help='Max backfill sequences to process before re-checking for new live sequences (default: 1000)'
        )
        parser.add_argument(
            '--rollup-interval', type=int, default=120,
            help='Minimum seconds between dashboard rollup rebuilds (default: 120)'
        )
        parser.add_argument(
            '--reset', action='store_true',
            help='Reset live polling to start from the current latest sequence and restart the '
                 'backfill window from there, then exit without entering the poll loop. Run this '
                 'once as a one-off command, never as the long-running process, so a crash/restart '
                 "doesn't wipe out backfill progress."
        )

    def handle(self, *args, **options):
        interval = options['interval']
        start_arg = options['start']
        backfill_days = options['backfill_days']
        sequences_per_day = options['sequences_per_day']
        backfill_batch_size = options['backfill_batch_size']
        rollup_interval = options['rollup_interval']

        if options['reset']:
            self._reset(start_arg, backfill_days, sequences_per_day)
            return

        logger.info("Starting sequence poller")

        last_rollup_refresh = 0.0

        while True:
            try:
                latest = fetch_latest_sequence()

                state = SequenceState.objects.first()
                if state is None:
                    env_start = os.environ.get('INITIAL_SEQUENCE')
                    if start_arg is not None:
                        forward_start = start_arg
                    elif env_start is not None:
                        forward_start = int(env_start)
                    else:
                        forward_start = latest
                    state = SequenceState.objects.create(last_sequence=forward_start - 1)
                    logger.info(
                        "First run, starting live polling",
                        extra={'osm.sequence.forward_start': forward_start},
                    )

                if state.backfill_sequence is None:
                    state.backfill_sequence = state.last_sequence
                    state.backfill_floor = max(1, state.last_sequence - sequences_per_day * backfill_days)
                    state.save()
                    logger.info(
                        "Initialized backfill window",
                        extra={
                            'osm.sequence.backfill_start': state.backfill_sequence,
                            'osm.sequence.backfill_floor': state.backfill_floor,
                        },
                    )

                did_work = False

                # Live sequences take priority so the dashboard always has current data.
                if latest > state.last_sequence:
                    live_from = state.last_sequence + 1
                    state.batch_start = state.last_sequence
                    state.batch_target = latest
                    state.save()

                    for seq in range(live_from, latest + 1):
                        logger.debug("Processing sequence (live)", extra={'osm.sequence_number': seq})
                        process_sequence(seq)
                        state.last_sequence = seq
                        state.save()

                    logger.info(
                        "Live polling caught up",
                        extra={
                            'osm.sequence.range_start': live_from,
                            'osm.sequence.range_end': latest,
                            'osm.sequence.count': latest - live_from + 1,
                        },
                    )
                    did_work = True

                # Then spend a bounded batch filling in older history, so live
                # polling gets re-checked regularly instead of blocking on it.
                if state.backfill_sequence >= state.backfill_floor:
                    batch_end = max(state.backfill_floor, state.backfill_sequence - backfill_batch_size + 1)
                    batch_start = state.backfill_sequence
                    for seq in range(batch_start, batch_end - 1, -1):
                        logger.debug("Processing sequence (backfill)", extra={'osm.sequence_number': seq})
                        process_sequence(seq)
                        state.backfill_sequence = seq - 1
                        state.save()

                    logger.info(
                        "Backfill batch processed",
                        extra={
                            'osm.sequence.range_start': batch_end,
                            'osm.sequence.range_end': batch_start,
                            'osm.sequence.count': batch_start - batch_end + 1,
                            'osm.sequence.backfill_remaining': state.backfill_sequence - state.backfill_floor,
                        },
                    )
                    did_work = True
                elif state.backfill_floor is not None:
                    logger.info(
                        "Backfill complete",
                        extra={'osm.sequence.backfill_floor': state.backfill_floor},
                    )
                    state.backfill_floor = None
                    state.save()

                if time.monotonic() - last_rollup_refresh >= rollup_interval:
                    refresh_start = time.monotonic()
                    refresh_rollups()
                    last_rollup_refresh = time.monotonic()
                    logger.info(
                        "Rollups refreshed",
                        extra={'osm.rollup_refresh_seconds': round(last_rollup_refresh - refresh_start, 2)},
                    )

                if not did_work:
                    logger.info(
                        "Up to date, sleeping",
                        extra={
                            'osm.sequence.last': state.last_sequence,
                            'osm.sequence.latest': latest,
                            'osm.poll_interval_seconds': interval,
                        },
                    )
                    time.sleep(interval)

            except CommandError:
                raise
            except Exception:
                logger.exception("Error in poll loop")
                time.sleep(interval)

    def _reset(self, start_arg, backfill_days, sequences_per_day):
        latest = fetch_latest_sequence()
        forward_start = start_arg if start_arg is not None else latest

        state = SequenceState.objects.first()
        if state is None:
            state = SequenceState.objects.create(last_sequence=forward_start - 1)
        else:
            state.last_sequence = forward_start - 1

        state.backfill_sequence = forward_start - 1
        state.backfill_floor = max(1, forward_start - sequences_per_day * backfill_days)
        state.batch_start = None
        state.batch_target = None
        state.save()

        logger.info(
            "Reset poller state",
            extra={
                'osm.sequence.forward_start': forward_start,
                'osm.sequence.backfill_start': state.backfill_sequence,
                'osm.sequence.backfill_floor': state.backfill_floor,
            },
        )
