from django.core.management.base import BaseCommand
from django.db import connection

from changesets.ingest.reconcile import reconcile, stale_open_changesets


class Command(BaseCommand):
    help = (
        'Re-fetch from the OSM API the changesets still stored as open more than 25 h after '
        'creation (the replication feed missed their closing update) and write them. The poller '
        'does this hourly for recent days; run this for a wider range.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='How far back to look, by creation date (default: 30)')
        parser.add_argument('--dry-run', action='store_true', help='Only count them')

    def handle(self, *args, **options):
        # The Timescale writer's existence checks and deletes can run past the
        # role's default statement timeout over a large batch.
        connection.cursor().execute("SET statement_timeout = 0")
        ids = stale_open_changesets(options['days'])
        self.stdout.write(f'{len(ids)} changesets still open more than 25 h after creation')
        if options['dry_run'] or not ids:
            return
        fetched, still_open = reconcile(ids)
        self.stdout.write(f'fetched {fetched}, still open {still_open}')
