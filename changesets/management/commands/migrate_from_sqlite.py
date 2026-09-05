from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = (
        'One-time copy of Changeset/SequenceState/ImportJob rows from a legacy SQLite '
        'file into the current (default) database, preserving primary keys. Run with '
        'writes to the source stopped — this does not lock or coordinate with anything.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--sqlite-path', required=True, help='Path to the legacy db.sqlite3 file')
        parser.add_argument('--batch-size', type=int, default=5000)

    def handle(self, *args, **options):
        sqlite_path = options['sqlite_path']
        batch_size = options['batch_size']

        # Registering an alias after startup skips ConnectionHandler's normal
        # defaulting pass, so the dict has to be fully formed up front.
        connections.databases['legacy_sqlite'] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': sqlite_path,
            'USER': '', 'PASSWORD': '', 'HOST': '', 'PORT': '',
            'ATOMIC_REQUESTS': False,
            'AUTOCOMMIT': True,
            'CONN_MAX_AGE': 0,
            'CONN_HEALTH_CHECKS': False,
            'OPTIONS': {},
            'TIME_ZONE': None,
            'TEST': {'CHARSET': None, 'COLLATION': None, 'MIGRATE': True, 'MIRROR': None, 'NAME': None},
        }

        # Import here so the model classes bind after the extra alias is registered.
        from changesets.models import Changeset, SequenceState, ImportJob

        for model in (Changeset, SequenceState, ImportJob):
            self._copy_model(model, batch_size)

        self.stdout.write(self.style.SUCCESS('Done.'))

    def _copy_model(self, model, batch_size):
        name = model.__name__
        source_qs = model.objects.using('legacy_sqlite').order_by('pk')
        total = source_qs.count()
        self.stdout.write(f'{name}: {total} rows to copy')

        copied = 0
        batch = []
        for obj in source_qs.iterator(chunk_size=batch_size):
            obj._state.db = 'default'
            obj._state.adding = True
            batch.append(obj)
            if len(batch) >= batch_size:
                model.objects.using('default').bulk_create(batch, batch_size=batch_size, ignore_conflicts=False)
                copied += len(batch)
                self.stdout.write(f'{name}: {copied}/{total}')
                batch = []
        if batch:
            model.objects.using('default').bulk_create(batch, batch_size=batch_size, ignore_conflicts=False)
            copied += len(batch)

        self.stdout.write(f'{name}: copied {copied} rows')

        dest_count = model.objects.using('default').count()
        if dest_count != total:
            raise CommandError(f'{name}: row count mismatch after copy — source {total}, dest {dest_count}')

        # Reset the destination's auto-increment sequence past the copied PKs,
        # so the next auto-generated id doesn't collide with one we just copied.
        with connections['default'].cursor() as cursor:
            table = model._meta.db_table
            cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                f"(SELECT MAX(id) FROM {table}) IS NOT NULL)"
            )
