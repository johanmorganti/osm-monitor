from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Q
from changesets.models import Changeset, FilterValue

# Known non-value strings some editing tools write as the literal
# `imagery_used` tag when no aerial imagery was used (e.g. `imagery_used=None`).
# osm_fetcher.py's derivation used to parse these into a non-empty *string*
# instead of real NULL — see TODO.md's former "imagery_family stores the
# literal string 'None'" entry. This command fixes rows already imported
# before that derivation was corrected.
NON_VALUES = ('none', 'unknown', 'n/a')


class Command(BaseCommand):
    help = (
        "Backfill imagery_family for changesets where it was set to a known "
        "non-value string (e.g. 'None', case-insensitive) instead of NULL, "
        "and clean up the resulting FilterValue rows."
    )

    def handle(self, *args, **options):
        non_value_filter = Q()
        for v in NON_VALUES:
            non_value_filter |= Q(imagery_family__iexact=v)

        qs = Changeset.objects.filter(non_value_filter)
        total = qs.count()
        self.stdout.write(f'Found {total} changesets with a non-value imagery_family...')

        # Matching rows live mostly in uncompressed chunks, but at least one
        # already-compressed chunk (see TODO.md's "Compression backlog" entry
        # — only one chunk is compressed so far) also has matches. Postgres/
        # TimescaleDB won't let a DML UPDATE decompress more than
        # max_tuples_decompressed_per_dml_transaction (default 100k) tuples
        # in one transaction; raising it here is safe because we already
        # know the bound (one compressed chunk, ~130k tuples) rather than
        # guessing at an unbounded number.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0")
            updated = qs.update(imagery_family=None)
        self.stdout.write(self.style.SUCCESS(f'Updated {updated}/{total} changesets to imagery_family=NULL.'))

        fv_filter = Q(field='imagery')
        fv_value_filter = Q()
        for v in NON_VALUES:
            fv_value_filter |= Q(value__iexact=v)
        fv_qs = FilterValue.objects.filter(fv_filter & fv_value_filter)
        fv_count = fv_qs.count()
        deleted, _ = fv_qs.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted}/{fv_count} stale FilterValue rows.'))
