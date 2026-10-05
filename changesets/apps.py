from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _sqlite_pragmas(sender, connection, **kwargs):
    """WAL so readers (web) never block writers (the pollers) and the
    reverse; NORMAL sync is safe with WAL (a crash can lose the last
    transaction, never corrupt the file)."""
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')


class ChangesetsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'changesets'

    def ready(self):
        connection_created.connect(_sqlite_pragmas)
