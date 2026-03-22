from django.core.management.base import BaseCommand
from changesets.models import Changeset
from urllib.parse import urlparse


def derive_imagery_family(imageries):
    if not imageries:
        return None
    raw = imageries[0]
    if raw.startswith('http'):
        family = urlparse(raw).netloc or raw
    else:
        family = raw.split(' ')[0].split('/')[0].split('(')[0].strip()
    return family or None


class Command(BaseCommand):
    help = 'Backfill imagery_family for changesets that have imagery_used but no imagery_family'

    def handle(self, *args, **options):
        qs = Changeset.objects.filter(imagery_used__isnull=False, imagery_family__isnull=True)
        total = qs.count()
        self.stdout.write(f'Backfilling {total} changesets...')

        updated = 0
        for cs in qs.iterator():
            family = derive_imagery_family(cs.imagery_used)
            if family:
                cs.imagery_family = family
                cs.save(update_fields=['imagery_family'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f'Done. Updated {updated}/{total} changesets.'))
