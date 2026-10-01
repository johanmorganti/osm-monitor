import os
from pathlib import Path

from django.core.management.base import BaseCommand

from changesets.analytics.clickhouse.client import get_client

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / 'analytics' / 'clickhouse' / 'schema'


class Command(BaseCommand):
    help = (
        'Apply the ClickHouse schema (changesets/analytics/clickhouse/schema/*.sql, in name '
        'order). Every statement is idempotent (CREATE ... IF NOT EXISTS), so it is safe to '
        'run on every deploy.'
    )

    def handle(self, *args, **options):
        client = get_client()
        for path in sorted(SCHEMA_DIR.glob('*.sql')):
            # Drop full-line comments first: a ';' inside one must not split a statement.
            sql = '\n'.join(line for line in path.read_text().splitlines() if not line.lstrip().startswith('--'))
            for statement in (s.strip() for s in sql.split(';')):
                if statement:
                    client.command(statement)
            self.stdout.write(f'applied {path.name}')

        # Least-privilege monitoring user for the Datadog ClickHouse check + DBM
        # (system tables only), when Datadog is configured. Same pattern as
        # db/init/01-datadog.sh for Postgres.
        password = os.environ.get('DD_CLICKHOUSE_PASSWORD')
        if password:
            quoted = "'" + password.replace('\\', '\\\\').replace("'", "\\'") + "'"
            client.command(f'CREATE USER IF NOT EXISTS datadog IDENTIFIED BY {quoted}')
            client.command(f'ALTER USER datadog IDENTIFIED BY {quoted}')
            client.command('GRANT SELECT ON system.* TO datadog')
            self.stdout.write('datadog monitoring user ready')
