"""TimescaleDB writer: the `changesets_changeset` hypertable, via the ORM.

Upserts by checking which changesets already exist (one query per batch),
deleting the ones that grew and re-inserting them with everything new. After a
backfill, the continuous aggregates and FilterValue need an explicit refresh
over the range (they only refresh the last 7 days on their own).
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from ...cagg_maintenance import refresh_caggs_over_range
from ...models import Changeset
from ...rollups import refresh_filter_values_over_range
from .base import WriteResult

logger = logging.getLogger(__name__)


class TimescaleWriter:
    name = 'timescale'

    def write(self, records, log_extra):
        if not records:
            return WriteResult(0, 0, 0)
        # A replication sequence (or dump slice) covers one narrow time window,
        # so bounding the existence check by it lets chunk exclusion skip every
        # other chunk, compressed ones included. Derived from the batch's own
        # data, so it's right for live and backfill batches alike.
        created_ats = [r['created_at'] for r in records]
        existing_changes_count_by_id = dict(
            Changeset.objects.filter(
                changeset_id__in=[r['changeset_id'] for r in records],
                created_at__gte=min(created_ats),
                created_at__lte=max(created_ats),
            ).values_list('changeset_id', 'changes_count')
        )

        to_create = []
        to_delete = []  # (changeset_id, created_at) pairs, not bare ids
        skipped = updated = 0
        for record in records:
            changeset_id = record['changeset_id']
            existing = existing_changes_count_by_id.get(changeset_id)
            if existing is not None:
                incoming = record.get('changes_count') or 0
                extra = {**log_extra, 'osm.changeset_id': changeset_id,
                         'osm.changes_count.existing': existing, 'osm.changes_count.incoming': incoming}
                if existing >= incoming:
                    logger.debug("Changeset already up to date, skipping", extra=extra)
                    skipped += 1
                    continue
                logger.debug("Changeset has grown, updating", extra=extra)
                to_delete.append((changeset_id, record['created_at']))
                updated += 1
            to_create.append(record)

        if to_delete:
            self._delete(to_delete)
        if to_create:
            self._insert(to_create, log_extra)
        return WriteResult(len(to_create), skipped, updated)

    def _delete(self, pairs):
        # created_at is immutable once a changeset exists, so filtering by it
        # (not just changeset_id) is what lets chunk exclusion target only the
        # chunks the rows live in. But an OR of (changeset_id, created_at)
        # pairs alone only gets chunk exclusion for a handful of pairs:
        # measured, from ~10 pairs on the planner gives up and the DELETE locks
        # every chunk of the hypertable (and its compressed counterpart), which
        # deadlocked with compress_chunk on a 2006 chunk. So: one DELETE per
        # day, each ANDed with that day's plain created_at range. Per day rather
        # than one min..max range, since a batch can mix today's changesets with
        # an old one resurfacing through a comment (CLAUDE.md's "Old-dated
        # rows"), and a min..max range would span every chunk in between.
        by_day = defaultdict(list)
        for changeset_id, created_at in pairs:
            by_day[created_at.date()].append((changeset_id, created_at))
        for day, day_pairs in by_day.items():
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            delete_filter = Q()
            for changeset_id, created_at in day_pairs:
                delete_filter |= Q(changeset_id=changeset_id, created_at=created_at)
            Changeset.objects.filter(
                delete_filter,
                created_at__gte=day_start,
                created_at__lt=day_start + timedelta(days=1),
            ).delete()

    def _insert(self, records, log_extra):
        # The trigger derives `centroid` from the geohash locate() set (migration 0057).
        try:
            Changeset.objects.bulk_create(
                [Changeset(**record) for record in records],
                ignore_conflicts=True,  # skip any duplicate that somehow made it through
            )
        except Exception:
            logger.exception("Bulk creation failed, falling back to individual creation", extra=log_extra)
            for record in records:
                try:
                    Changeset.objects.create(**record)
                except Exception:
                    logger.exception(
                        "Error creating changeset",
                        extra={**log_extra, 'osm.changeset_id': record.get('changeset_id')},
                    )

    def after_backfill(self, start, end, stdout=None):
        """Bulk writes land far outside every CAgg policy's 7-day window (see
        CLAUDE.md's "Old-dated rows"), and FilterValue (autocomplete, exact-value
        filters) only tracks the live poller's progress, so both need a
        range-scoped refresh."""
        if stdout:
            stdout.write(f'Refreshing all CAggs over {start.date()}..{end.date()}...')
        refresh_caggs_over_range(start, end, stdout=stdout)
        if stdout:
            stdout.write('Refreshing FilterValue over the same range...')
        refresh_filter_values_over_range(start, end)
