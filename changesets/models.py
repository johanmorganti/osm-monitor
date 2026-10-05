"""The app's own state, in SQLite: the pollers' positions. Every changeset
and object lives in ClickHouse (changesets/analytics/clickhouse/schema/)."""
from django.db import models


class SequenceState(models.Model):
    """poll_sequences' position (singleton row): the changeset feed's last
    sequence written, the current catch-up batch, and the backward backfill."""
    last_sequence = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)
    batch_start = models.IntegerField(null=True, blank=True)
    batch_target = models.IntegerField(null=True, blank=True)
    # Backfill walks backward in time from where live polling started, so recent
    # data is available immediately while older history fills in behind it.
    backfill_sequence = models.IntegerField(null=True, blank=True)
    backfill_floor = models.IntegerField(null=True, blank=True)

    class Meta:
        app_label = 'changesets'

    @classmethod
    def get_last(cls):
        obj = cls.objects.first()
        return obj.last_sequence if obj else None


class DiffState(models.Model):
    """poll_diffs' position (singleton row). Live: the last minutely diff
    written. Backfill: daily diffs, walked backward from the day before live
    started (`backfill_day`, the next one to write) down to `backfill_floor`
    (None once done). A daily diff holds exactly the minutely diffs stamped
    within its day, so the two ranges meet without overlapping."""
    last_minute = models.IntegerField()
    backfill_day = models.IntegerField(null=True, blank=True)
    backfill_floor = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'changesets'
