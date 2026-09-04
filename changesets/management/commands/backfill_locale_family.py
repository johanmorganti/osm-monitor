from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Backfill locale_family for changesets that have locale but no locale_family'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM changesets_changeset
                WHERE locale IS NOT NULL AND locale_family IS NULL
            """)
            total = cursor.fetchone()[0]
            self.stdout.write(f'Backfilling {total} changesets...')

            # SQLite: extract language code by taking the part before - or _
            cursor.execute("""
                UPDATE changesets_changeset
                SET locale_family = UPPER(
                    CASE
                        WHEN INSTR(REPLACE(locale, '_', '-'), '-') > 0
                        THEN SUBSTR(REPLACE(locale, '_', '-'), 1, INSTR(REPLACE(locale, '_', '-'), '-') - 1)
                        ELSE locale
                    END
                )
                WHERE locale IS NOT NULL AND locale_family IS NULL
            """)
            updated = cursor.rowcount

        self.stdout.write(self.style.SUCCESS(f'Done. Updated {updated}/{total} changesets.'))
