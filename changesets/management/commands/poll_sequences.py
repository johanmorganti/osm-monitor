import time
import os
import requests
import yaml
from django.core.management.base import BaseCommand, CommandError
from changesets.models import SequenceState
from changesets.osm_fetcher import process_sequence


PLANET_STATE_URL = 'https://planet.osm.org/replication/changesets/state.yaml'


def fetch_latest_sequence():
    response = requests.get(PLANET_STATE_URL, stream=True, timeout=30)
    response.raise_for_status()
    data = yaml.safe_load(response.raw.read())
    return int(data['sequence'])


class Command(BaseCommand):
    help = 'Poll planet.osm.org for new changeset sequences and ingest them continuously'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval', type=int, default=60,
            help='Seconds to sleep between polls (default: 60)'
        )
        parser.add_argument(
            '--start', type=int, default=None,
            help='Starting sequence number (required on first run if DB is empty)'
        )

    def handle(self, *args, **options):
        interval = options['interval']
        start_arg = options['start']

        self.stdout.write('Starting sequence poller...')

        while True:
            try:
                latest = fetch_latest_sequence()
                self.stdout.write(f'Latest sequence on planet: {latest}')

                last = SequenceState.get_last()

                if last is None:
                    # First run — need a starting point
                    env_start = os.environ.get('INITIAL_SEQUENCE')
                    if start_arg is not None:
                        last = start_arg - 1
                    elif env_start is not None:
                        last = int(env_start) - 1
                    else:
                        raise CommandError(
                            'No sequence state in DB. Provide --start <seq> or set '
                            'INITIAL_SEQUENCE env var to avoid importing all history.'
                        )
                    self.stdout.write(f'First run, starting from sequence {last + 1}')

                if latest <= last:
                    self.stdout.write(f'No new sequences (last={last}, latest={latest}). Sleeping {interval}s.')
                    time.sleep(interval)
                    continue

                # Record batch boundaries before starting
                state = SequenceState.objects.first()
                if state is None:
                    SequenceState.objects.create(last_sequence=last, batch_start=last, batch_target=latest)
                else:
                    state.batch_start = last
                    state.batch_target = latest
                    state.save()

                for seq in range(last + 1, latest + 1):
                    self.stdout.write(f'Processing sequence {seq}...')
                    process_sequence(seq)

                    # Update state after each sequence so we can resume on crash
                    state = SequenceState.objects.first()
                    if state is None:
                        SequenceState.objects.create(last_sequence=seq)
                    else:
                        state.last_sequence = seq
                        state.save()

                self.stdout.write(self.style.SUCCESS(
                    f'Caught up to sequence {latest}. Sleeping {interval}s.'
                ))

            except CommandError:
                raise
            except Exception as e:
                self.stderr.write(f'Error: {e}')

            time.sleep(interval)
