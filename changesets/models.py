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
    # Not unique=True here — TimescaleDB requires every UNIQUE constraint on
    # a hypertable to include the partitioning column, so real duplicate-
    # import protection is the composite UNIQUE(changeset_id, created_at) in
    # Meta.constraints instead (see migration 0018_timescale_hypertable).
    changeset_id = models.BigIntegerField()
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
        # Replaces changeset_id's old standalone unique=True — TimescaleDB
        # requires the partitioning column (created_at) in any UNIQUE
        # constraint on a hypertable.
        constraints = [
            models.UniqueConstraint(fields=['changeset_id', 'created_at'], name='changeset_changeset_id_created_at_uniq'),
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


class CaggVolumeHourly(models.Model):
    """Unmanaged mapping onto the cagg_volume_hourly continuous aggregate
    (see migration 0020_continuous_aggregates) — replaces DailyVolume.
    managed=False: Django never creates/migrates this table, TimescaleDB
    owns its schema and refresh via the CA's own policy."""
    bucket = models.DateTimeField(primary_key=True)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_volume_hourly'


class _CaggDaily(models.Model):
    """Shared shape for the four per-dimension daily continuous aggregates
    (cagg_editor_daily, cagg_imagery_daily, cagg_locale_daily,
    cagg_contributor_daily) — replaces DailyBreakdown's per-category rows,
    but as four separate CAs rather than one polymorphic table: TimescaleDB
    doesn't support UNION inside a continuous aggregate's defining query, so
    one combined view isn't possible (see docs/ARCHITECTURE.md)."""
    # primary_key=True here isn't a real uniqueness claim (the actual key is
    # the composite (bucket, name)) — it's only to stop Django implicitly
    # adding an `id` AutoField and then querying a column these views don't
    # have. Never relied on for .get()/pk lookups, only filter()/aggregate().
    bucket = models.DateTimeField(primary_key=True)
    name = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        abstract = True
        app_label = 'changesets'
        managed = False


class CaggEditorDaily(_CaggDaily):
    class Meta(_CaggDaily.Meta):
        db_table = 'cagg_editor_daily'


class CaggImageryDaily(_CaggDaily):
    class Meta(_CaggDaily.Meta):
        db_table = 'cagg_imagery_daily'


class CaggLocaleDaily(_CaggDaily):
    class Meta(_CaggDaily.Meta):
        db_table = 'cagg_locale_daily'


class CaggContributorDaily(_CaggDaily):
    class Meta(_CaggDaily.Meta):
        db_table = 'cagg_contributor_daily'


class CaggVolumeDaily(models.Model):
    """Unmanaged mapping onto the cagg_volume_daily continuous aggregate (see
    migration 0023) — the daily-grain counterpart to CaggVolumeHourly, used
    by TimeseriesView's ungrouped path for ranges too wide for hourly grain
    to stay near the ~300-point target (see _pick_interval in views.py).
    Closes the bug where a wide ungrouped range used to silently truncate to
    only the oldest 15 days of hourly data instead of showing the full range
    at a coarser grain."""
    bucket = models.DateTimeField(primary_key=True)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_volume_daily'


class _CaggHourly(models.Model):
    """Shared shape for the four per-dimension HOURLY continuous aggregates
    (cagg_editor_hourly, cagg_imagery_hourly, cagg_locale_hourly,
    cagg_contributor_hourly) — same shape as _CaggDaily, different grain.
    Exists so TimeseriesView's group_by paths can also auto-pick hourly for
    narrow ranges (see _pick_interval/CAGG_MODELS_HOURLY in views.py),
    instead of being stuck at daily grain regardless of range width."""
    bucket = models.DateTimeField(primary_key=True)  # not a real uniqueness claim — see _CaggDaily's comment
    name = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        abstract = True
        app_label = 'changesets'
        managed = False


class CaggEditorHourly(_CaggHourly):
    class Meta(_CaggHourly.Meta):
        db_table = 'cagg_editor_hourly'


class CaggImageryHourly(_CaggHourly):
    class Meta(_CaggHourly.Meta):
        db_table = 'cagg_imagery_hourly'


class CaggLocaleHourly(_CaggHourly):
    class Meta(_CaggHourly.Meta):
        db_table = 'cagg_locale_hourly'


class CaggContributorHourly(_CaggHourly):
    class Meta(_CaggHourly.Meta):
        db_table = 'cagg_contributor_hourly'


class CaggGeoDaily(models.Model):
    """Unmanaged mapping onto the cagg_geo_daily continuous aggregate (see
    migration 0025) — grid-cell (0.5-degree) changeset density per day, for
    GeoView. grid_lat/grid_lon are NULL for changesets whose bbox diagonal
    exceeds ~200km (see the migration for the exact expression): a stray
    far-away edited object blowing up an otherwise-local changeset's bbox
    means its centroid can't be trusted, so it's excluded from the map
    rather than plotted somewhere misleading. Not a subclass of _CaggDaily
    since it needs two dimension columns (lat/lon) instead of one (name).
    bucket as primary_key isn't a real uniqueness claim — see _CaggDaily's
    identical caveat; never relied on for .get()/pk lookups."""
    bucket = models.DateTimeField(primary_key=True)
    grid_lat = models.FloatField(null=True)
    grid_lon = models.FloatField(null=True)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_geo_daily'


class CaggGeoFineDaily(models.Model):
    """Unmanaged mapping onto the cagg_geo_fine_daily continuous aggregate
    (see migration 0027) — same shape as CaggGeoDaily, but at a finer grid
    (changesets/geo.py's FINE_GRID_SIZE_DEGREES, ~0.05°) for GeoView's
    zoomed-in map view. Always viewport-scoped by GeoView (filtered on
    grid_lat/grid_lon) rather than fetched globally like the coarse CAgg —
    even pre-aggregated, shipping every fine cell on Earth for one zoomed-in
    view would be excessive."""
    bucket = models.DateTimeField(primary_key=True)
    grid_lat = models.FloatField(null=True)
    grid_lon = models.FloatField(null=True)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_geo_fine_daily'


class FilterValue(models.Model):
    """Distinct known values for the dashboard's contributor/editor/imagery
    autocomplete. Deduplicated globally — unlike DailyBreakdown, there's no
    date dimension — so its size tracks the number of distinct contributors/
    editors/imageries ever seen, not the number of changesets, and stays
    small (editor/imagery) or slow-growing (contributor) regardless of how
    much history is imported. Populated incrementally by
    refresh_rollups_incremental() (see changesets.rollups) from new rows
    only; existing data is backfilled once via the backfill_filter_values
    management command."""
    FIELD_CHOICES = [
        ('contributor', 'Contributor'),
        ('editor', 'Editor'),
        ('imagery', 'Imagery'),
    ]
    field = models.CharField(max_length=20, choices=FIELD_CHOICES)
    value = models.CharField(max_length=255)

    class Meta:
        app_label = 'changesets'
        unique_together = [('field', 'value')]
