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
    """Singleton watermark row. `last_id` is refresh_rollups_incremental()'s
    watermark (see changesets.rollups) — the highest Changeset.id already
    merged into the now-unused DailyVolume/DailyBreakdown rollup tables;
    kept only for the manual refresh_rollups escape hatch, no longer
    advanced automatically (`docs/todo/continuous-aggregates-migration.md`).

    `last_created_at` is refresh_filter_values_incremental()'s watermark —
    deliberately a *different* column on a *different* field (`created_at`,
    the hypertable's partitioning column) rather than reusing `last_id`:
    filtering on `id` can't use chunk exclusion (confirmed cause of the
    dead-rollup-loop cost above), filtering on `created_at` can. NULL means
    "never run" (first call covers everything, not just rows after some
    default value)."""
    last_id = models.IntegerField(default=0)
    last_created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'changesets'


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

    # Derived from centroid by a database trigger (migration 0032), same as
    # centroid itself (migration 0029) — see changesets/geo.py. No
    # db_index=True here: the matching btree indexes already exist at the
    # DB level under custom names (see Meta.indexes below) rather than a
    # Django-auto-named one.
    geohash = models.CharField(max_length=12, null=True, blank=True)
    country_code = models.CharField(max_length=2, null=True, blank=True)

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

    class Meta:
        app_label = 'changesets'
        # The dashboard's contributor/editor/imagery/language/country filters
        # use __iexact, which Postgres implements as UPPER(col) = UPPER(val)
        # — without a matching expression index that forces a sequential
        # scan even when the date range is also filtered, since a date range
        # spanning most of the table's history isn't selective enough on its
        # own. locale_family's copy of this index (migration 0040) was added
        # later than the other three (migration 0015) — it predates
        # `language` becoming a real dashboard filter, so the raw-table
        # fallback used by ToplistView/GeoView for any active filter (see
        # CLAUDE.md) silently sequential-scanned on language specifically
        # until this was caught. country_code's (migration 0047) was added
        # proactively alongside `country` becoming a filter, specifically to
        # avoid repeating that mistake.
        indexes = [
            models.Index(Upper('user'), name='changeset_user_upper_idx'),
            models.Index(Upper('created_by_family'), name='changeset_editor_upper_idx'),
            models.Index(Upper('imagery_family'), name='changeset_imagery_upper_idx'),
            models.Index(Upper('locale_family'), name='changeset_locale_upper_idx'),
            models.Index(Upper('country_code'), name='changeset_country_upper_idx'),
            models.Index(fields=['geohash'], name='changeset_geohash_idx'),
            models.Index(fields=['country_code'], name='changeset_country_code_idx'),
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


class CaggCountryDaily(_CaggDaily):
    """Migration 0045 — replaces `language` as the dashboard's 5th
    dimension (language stays a valid API param, just no longer on the
    dashboard — see CLAUDE.md's dimension-naming table)."""
    class Meta(_CaggDaily.Meta):
        db_table = 'cagg_country_daily'


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


class CaggCountryHourly(_CaggHourly):
    class Meta(_CaggHourly.Meta):
        db_table = 'cagg_country_hourly'


class CaggGeoHashedDaily(models.Model):
    """Unmanaged mapping onto the cagg_geo_hashed_daily continuous aggregate
    (see migration 0033) — replaces CaggGeoDaily/CaggGeoFineDaily's two-
    CAgg/two-column (grid_lat/grid_lon) design with one CAgg keyed by the
    geohash column (migration 0032). A coarser cell is a shorter *prefix* of
    a finer one, so GeoView serves every zoom level by truncating `geohash`
    at query time rather than choosing between two pre-materialized grids.
    bucket as primary_key isn't a real uniqueness claim — see _CaggDaily's
    identical caveat; never relied on for .get()/pk lookups."""
    bucket = models.DateTimeField(primary_key=True)
    geohash = models.CharField(max_length=12)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_geo_hashed_daily'


# Nine cross-dimension CAggs answering "filter by one dimension, broken out
# by another" directly — the shape ToplistView's dimension param and
# TimeseriesView's group_by+filter both need. Deliberately not abstracted
# into a shared base like _CaggDaily/_CaggHourly: each pair's two name
# columns are named after their own dimension (contributor/editor/imagery/
# locale/country) for readability at the query site (views.py), so the
# field names genuinely differ per pair rather than being interchangeable.
#
# The 3 editor/imagery/language pairs (migration 0041) shipped first;
# the 3 contributor pairs (migration 0043) were deliberately deferred at
# that point — contributor has 344K distinct values vs. low hundreds for
# the other three, and every CAgg adds recurring refresh cost on this
# I/O-constrained host — then built anyway once the contributor-grouped
# toplist was confirmed to be the remaining slow path (see
# docs/todo/continuous-aggregates-migration.md). The 3 country pairs
# (migration 0048) shipped alongside country replacing language as the
# dashboard's 5th dimension — no country x language pair, since language
# has no dashboard caller left to cross it with.
class CaggEditorImageryDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)  # not a real uniqueness claim — see _CaggDaily's comment
    editor = models.CharField(max_length=255)
    imagery = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_editor_imagery_daily'


class CaggEditorLocaleDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    editor = models.CharField(max_length=255)
    locale = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_editor_locale_daily'


class CaggImageryLocaleDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    imagery = models.CharField(max_length=255)
    locale = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_imagery_locale_daily'


class CaggContributorEditorDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    contributor = models.CharField(max_length=255)
    editor = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_contributor_editor_daily'


class CaggContributorImageryDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    contributor = models.CharField(max_length=255)
    imagery = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_contributor_imagery_daily'


class CaggContributorLocaleDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    contributor = models.CharField(max_length=255)
    locale = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_contributor_locale_daily'


class CaggContributorCountryDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    contributor = models.CharField(max_length=255)
    country = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_contributor_country_daily'


class CaggCountryEditorDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    country = models.CharField(max_length=255)
    editor = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_country_editor_daily'


class CaggCountryImageryDaily(models.Model):
    bucket = models.DateTimeField(primary_key=True)
    country = models.CharField(max_length=255)
    imagery = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_country_imagery_daily'


class CaggEditorVersionDaily(models.Model):
    """Editor family -> exact created_by version string (e.g. "StreetComplete"
    -> "StreetComplete 55.0"), migration 0050/0051 — backs ToplistView's
    dimension=editor_version drill-down (requires an `editor` filter, see
    views.py's RAW_ONLY_DIMENSIONS)."""
    bucket = models.DateTimeField(primary_key=True)
    editor = models.CharField(max_length=255)
    version = models.CharField(max_length=255)
    cnt = models.BigIntegerField()
    changes_sum = models.BigIntegerField()

    class Meta:
        app_label = 'changesets'
        managed = False
        db_table = 'cagg_editor_version_daily'


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
