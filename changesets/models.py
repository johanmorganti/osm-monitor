from django.db import models
from django.db.models.functions import Upper
import json

class SequenceState(models.Model):
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


class RollupState(models.Model):
    """Singleton watermark for refresh_rollups_incremental() (see
    changesets.rollups): the highest Changeset.id already merged into the
    DailyVolume/DailyBreakdown rollup tables."""
    last_id = models.IntegerField(default=0)

    class Meta:
        app_label = 'changesets'


class ImportJob(models.Model):
    seq_start   = models.IntegerField()
    seq_end     = models.IntegerField()
    current_seq = models.IntegerField(null=True, blank=True)
    total       = models.IntegerField()
    status      = models.CharField(max_length=20, default='pending')  # pending, running, done, error
    error       = models.TextField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    @property
    def done(self):
        if self.current_seq is None:
            return 0
        return self.current_seq - self.seq_start + 1

    @property
    def pct(self):
        if self.total == 0:
            return 0
        return round(self.done / self.total * 100, 1)


class Changeset(models.Model):
    changeset_id = models.BigIntegerField(unique=True)
    created_at = models.DateTimeField(null=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    open = models.BooleanField(null=True)
    changes_count = models.IntegerField(null=True)
    user = models.CharField(max_length=255, null=True, db_index=True)
    user_id = models.IntegerField(null=True)
    min_lat = models.FloatField(null=True)
    max_lat = models.FloatField(null=True)
    min_lon = models.FloatField(null=True)
    max_lon = models.FloatField(null=True)
    comments_count = models.IntegerField(null=True)
    tags = models.JSONField(null=True)
    
    # New dedicated columns for common tags
    created_by = models.CharField(max_length=255, null=True, blank=True)
    created_by_family = models.CharField(max_length=255, null=True, blank=True, db_index=True)  # Base name of created_by (e.g., "StreetComplete")
    comment = models.TextField(null=True, blank=True)
    locale = models.CharField(max_length=255, null=True, blank=True)  # widened - some clients put more than a locale code here
    locale_family = models.CharField(max_length=255, null=True, blank=True, db_index=True)  # language code only, e.g. "FR" (widened - some clients put a full place name in the locale tag)
    source = models.CharField(max_length=255, null=True, blank=True)
    imagery_used = models.JSONField(null=True, blank=True)  # Store as array of strings
    imagery_family = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    host = models.CharField(max_length=255, null=True, blank=True)
    changesets_count = models.IntegerField(null=True, blank=True)
    hashtags = models.JSONField(null=True, blank=True)  # Store as array of strings
    streetcomplete_quest_type = models.CharField(max_length=255, null=True, blank=True)
    review_requested = models.BooleanField(null=True, blank=True)
    remaining_tags = models.JSONField(null=True, blank=True)  # Store all other tags

    @property
    def imagery_list(self):
        """Returns a list of imageries used in this changeset"""
        return self.imagery_used or []

    @property
    def hashtags_list(self):
        """Returns a list of hashtags used in this changeset"""
        return self.hashtags or []

    @property
    def remaining_tags_dict(self):
        """Returns a dictionary of remaining tags"""
        return self.remaining_tags or {}

    @property
    def tags_dict(self):
        """Returns a dictionary of all tags"""
        return self.tags or {}

    class Meta:
        app_label = 'changesets'
        # The dashboard's contributor/editor/imagery filters use __iexact,
        # which Postgres implements as UPPER(col) = UPPER(val) — without a
        # matching expression index that forces a sequential scan even when
        # the date range is also filtered, since a date range spanning most
        # of the table's history isn't selective enough on its own.
        indexes = [
            models.Index(Upper('user'), name='changeset_user_upper_idx'),
            models.Index(Upper('created_by_family'), name='changeset_editor_upper_idx'),
            models.Index(Upper('imagery_family'), name='changeset_imagery_upper_idx'),
        ]


class DailyVolume(models.Model):
    """Daily/hourly changeset volume, precomputed so the dashboard's unfiltered
    view doesn't rescan the whole Changeset table on every load. Rebuilt in
    full periodically (see changesets.rollups) rather than updated in place —
    always correct by construction, at the cost of a small staleness window."""
    date = models.DateField()
    hour = models.IntegerField()
    count = models.IntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        unique_together = [('date', 'hour')]


class DailyBreakdown(models.Model):
    """Per-day counts by editor/imagery/locale/contributor, precomputed for
    the same reason as DailyVolume. Only covers the *unfiltered* dashboard
    view — a contributor/editor/imagery filter isn't a dimension every row
    here carries, so those queries fall back to the raw Changeset table."""
    CATEGORY_CHOICES = [
        ('editor', 'Editor'),
        ('imagery', 'Imagery'),
        ('locale', 'Locale'),
        ('contributor', 'Contributor'),
    ]
    date = models.DateField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    name = models.CharField(max_length=255)
    count = models.IntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        unique_together = [('date', 'category', 'name')]
        indexes = [models.Index(fields=['category', 'name'])]
