from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.views.generic import TemplateView
from django.db.models import Count, Sum
from django.db.models.expressions import RawSQL
from django.db.models.functions import TruncDate, TruncHour, Substr
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiTypes
from .models import (
    Changeset, SequenceState, FilterValue,
    CaggVolumeHourly, CaggVolumeDaily,
    CaggEditorDaily, CaggImageryDaily, CaggLocaleDaily, CaggContributorDaily,
    CaggEditorHourly, CaggImageryHourly, CaggLocaleHourly, CaggContributorHourly,
    CaggGeoHashedDaily,
    CaggEditorImageryDaily, CaggEditorLocaleDaily, CaggImageryLocaleDaily,
    CaggContributorEditorDaily, CaggContributorImageryDaily, CaggContributorLocaleDaily,
)
from .serializers import ChangesetSerializer
from .geo import (
    GEOHASH_PREFIX_LENGTH, viewport_overlap_sql,
    geohash_cell_size_degrees, geohash_decode_center, geohash_precision_for_bbox,
    geohash_bbox_prefix, geohash_prefix_range_sql, GEOHASH_PREFIX_UPPER_BOUND_CHAR,
)
from collections import defaultdict
from datetime import datetime, timedelta

class ChangesetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


class ChangesetQueryView(APIView):
    """Paginated list of raw changeset records, newest first.

    Always bounded by a date range — defaults to the last 24 hours when
    start_date/end_date aren't given. An unbounded "list everything" mode
    isn't offered: with tens of millions of rows, both the full scan to
    build a page and the COUNT(*) DRF's pagination needs for it are
    prohibitively expensive, and nobody actually wants to page through all
    of OSM history 100 rows at a time anyway. Use TimeseriesView/SummaryView/
    ToplistView for aggregate questions over a large range instead of
    pulling raw rows."""

    # Not used for actual pagination (that's done manually below with the
    # same class) — set here so drf-spectacular knows to document the
    # paginated envelope shape (count/next/previous/results).
    pagination_class = ChangesetPagination

    @extend_schema(
        tags=['changesets'],
        summary='List changesets',
        description=(
            'Paginated list of ingested changeset records, newest first, with optional filters. '
            'Always bounded by a date range — defaults to the last 24 hours if start_date/end_date '
            'are omitted (there\'s no unfiltered "everything" mode; use /api/changesets/timeseries/, '
            '/summary/, or /toplist/ for aggregate questions over a large range).'
        ),
        parameters=[
            OpenApiParameter('start_date', OpenApiTypes.DATE, description='Only changesets created on/after this date (YYYY-MM-DD). Defaults to 24 hours before now.'),
            OpenApiParameter('end_date', OpenApiTypes.DATE, description='Only changesets created on/before this date (YYYY-MM-DD), inclusive of the whole day. Defaults to now.'),
            OpenApiParameter('user', OpenApiTypes.STR, description='Exact OSM username.'),
            OpenApiParameter('editor', OpenApiTypes.STR, description='Exact editor family (e.g. "StreetComplete", "iD").'),
            OpenApiParameter('hashtag', OpenApiTypes.STR, description='Hashtag the changeset must include (from its #hashtags tag).'),
            OpenApiParameter('imagery_raw', OpenApiTypes.STR, description='Exact raw imagery string as found in the changeset\'s imagery_used tag.'),
            OpenApiParameter('imagery_family', OpenApiTypes.STR, description='Normalised imagery family (case-insensitive), e.g. "Bing".'),
            OpenApiParameter('bbox', OpenApiTypes.STR, description='Bounding box filter as "min_lon,min_lat,max_lon,max_lat".'),
            OpenApiParameter('page', OpenApiTypes.INT, description='Page number.'),
            OpenApiParameter('page_size', OpenApiTypes.INT, description='Results per page (default 100, max 1000).'),
        ],
        responses=ChangesetSerializer(many=True),
    )
    def get(self, request):
        qs = Changeset.objects.order_by('-created_at')

        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user = request.query_params.get('user')
        editor = request.query_params.get('editor')
        hashtag = request.query_params.get('hashtag')
        imagery_raw = request.query_params.get('imagery_raw')
        imagery_family = request.query_params.get('imagery_family')
        bbox = request.query_params.get('bbox')

        # Explicit start_date/end_date are whole-day (YYYY-MM-DD) boundaries;
        # a missing one defaults off the precise current instant instead, so
        # "no dates given" is a real rolling 24h window, not just "today".
        now = timezone.now()
        end_dt = (datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)) if end_date else now
        start_dt = start_date if start_date else (now - timedelta(hours=24))

        qs = qs.filter(created_at__gte=start_dt)
        qs = qs.filter(created_at__lt=end_dt)
        if user:
            qs = qs.filter(user=user)
        if editor:
            qs = qs.filter(created_by_family=editor)
        if hashtag:
            qs = qs.filter(hashtags__contains=[hashtag])
        if imagery_raw:
            qs = qs.filter(imagery_used__contains=[imagery_raw])
        if imagery_family:
            qs = qs.filter(imagery_family__iexact=imagery_family)
        if bbox:
            try:
                min_lon, min_lat, max_lon, max_lat = [float(v) for v in bbox.split(',')]
                qs = qs.filter(
                    min_lat__gte=min_lat, max_lat__lte=max_lat,
                    min_lon__gte=min_lon, max_lon__lte=max_lon,
                )
            except (ValueError, TypeError):
                return Response({'error': 'bbox must be min_lon,min_lat,max_lon,max_lat'}, status=status.HTTP_400_BAD_REQUEST)

        paginator = ChangesetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ChangesetSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class DashboardView(TemplateView):
    """Thin HTML shell — no DB access. Chart data is fetched client-side from
    TimeseriesView/SummaryView/ToplistView so the page paints almost
    instantly; see dashboard.js."""
    template_name = 'changesets/dashboard.html'


# cagg_imagery_daily/cagg_locale_daily (migration 0022) bucket changesets with
# no imagery/language tag under this literal name instead of excluding them —
# a large NULL-tag campaign (e.g. a MapRoulette bulk edit) would otherwise be
# fully invisible in those two breakdowns despite counting toward total volume
# (see TODO.md). Clicking that bar / filtering by it needs to match real
# NULL rows, not the literal string, hence the isnull check in
# _filtered_changesets below. cagg_editor_daily/cagg_contributor_daily were
# left excluding NULLs — created_by_family/user are essentially always
# populated in practice, so there's no equivalent gap there.
NONE_BUCKET = '(none)'

# Dimension name (as used in the public API and in CAGG_MODELS below) -> the
# raw Changeset field it corresponds to. Shared by TimeseriesView's group_by
# and ToplistView's dimension.
DIMENSION_FIELDS = {
    'contributor': 'user',
    'editor': 'created_by_family',
    'imagery': 'imagery_family',
    'language': 'locale_family',
}

# One continuous aggregate per dimension (see migration 0020) — used for
# TimeseriesView's group_by and ToplistView's dimension (all 4 valid here),
# and by _single_filter_dimension's fast path (contributor/editor/imagery/
# language can each be a single filter now).
CAGG_MODELS = {
    'contributor': CaggContributorDaily,
    'editor': CaggEditorDaily,
    'imagery': CaggImageryDaily,
    'language': CaggLocaleDaily,
}

# Hourly counterpart to CAGG_MODELS (migration 0023) — same per-dimension
# NULL handling as their daily equivalents (editor/contributor exclude NULLs,
# imagery/language bucket them as NONE_BUCKET). Used by TimeseriesView's
# _pick_interval-driven grain choice: hourly for ranges narrow enough to stay
# near the ~300-point target, daily otherwise. Only TimeseriesView needs
# this — SummaryView/ToplistView aggregate over the whole range regardless
# of grain, so they stay on the (cheaper, pre-existing) daily CAs.
CAGG_MODELS_HOURLY = {
    'contributor': CaggContributorHourly,
    'editor': CaggEditorHourly,
    'imagery': CaggImageryHourly,
    'language': CaggLocaleHourly,
}

# Ungrouped-volume counterpart to the hourly/daily split above — no
# dimension, just total volume. CaggVolumeDaily (migration 0023) closes the
# gap where a wide ungrouped range used to silently truncate to the oldest
# 15 days of hourly data instead of showing the full range at a coarser
# grain (see _pick_interval).
CAGG_VOLUME_MODELS = {'hour': CaggVolumeHourly, 'day': CaggVolumeDaily}

TARGET_POINTS = 300
MAX_EXPLICIT_POINTS = 5000  # only constrains an explicit interval=hour override, see _pick_interval


def _pick_interval(start_date, end_date, interval_param):
    """Resolve 'hour' or 'day' for a TimeseriesView query. interval_param is
    the already-validated interval= query param (None, 'hour', or 'day').

    Auto-pick (interval_param is None): hourly if the range's hour-count fits
    within TARGET_POINTS (~300 -> ranges up to ~12.5 days), else daily — the
    only two grains that exist today. A 3rd (weekly/monthly) tier is
    intentionally not built yet (see TODO.md) — today's real data span is
    ~13 months, daily alone tops out around ~400 points, nowhere near a
    problem worth a third grain for.

    Explicit override: trusted, not silently capped — EXCEPT interval=hour
    on a range wide enough to blow past MAX_EXPLICIT_POINTS is rejected
    (raises ValueError, caller turns this into a 400), not truncated. Silent
    truncation on a wide range is exactly the bug this function replaces;
    doing it "safely" for the override path would just reintroduce it.
    interval=day has no such ceiling — not a concern at today's range widths.
    """
    range_days = (datetime.strptime(end_date, '%Y-%m-%d').date()
                  - datetime.strptime(start_date, '%Y-%m-%d').date()).days + 1
    hour_count = range_days * 24
    if interval_param == 'hour' and hour_count > MAX_EXPLICIT_POINTS:
        raise ValueError(
            f'interval=hour over this {range_days}-day range would return {hour_count} points '
            f'(max {MAX_EXPLICIT_POINTS}) — use interval=day or narrow the range'
        )
    if interval_param is not None:
        return interval_param
    return 'hour' if hour_count <= TARGET_POINTS else 'day'


def _format_bucket(bucket, interval):
    """Render a CA/raw-query bucket as the API's date-label string. `bucket`
    is a full datetime for every CA-backed path and for the raw path's
    TruncHour annotation, but a plain date for the raw path's TruncDate
    annotation (Django) — hasattr(bucket, 'date') tells the two apart
    without the caller needing to know which query produced it.

    Hour is always zero-padded (unlike this codebase's pre-existing unpadded
    f'{hour}:00', which only got away with it by sorting on the real
    datetime column first) — once group_by can also resolve to hourly,
    callers sort on these *formatted strings* (per-name/per-bucket pivot),
    where an unpadded "...9:00" would sort after "...10:00" and corrupt the
    x-axis. dashboard.js's shortDate() only regex-matches the date prefix,
    so this is a display-only, frontend-compatible change."""
    day = bucket.date() if hasattr(bucket, 'date') else bucket
    return f'{day.isoformat()} {bucket.hour:02d}:00' if interval == 'hour' else day.isoformat()


def _single_filter_dimension(contributor, editor, imagery, language):
    """(dimension, value) if exactly one of contributor/editor/imagery/language
    is set, else None. That's the only shape a per-dimension continuous
    aggregate can answer directly — SummaryView just needs a scalar total,
    which the matching CA already has pre-aggregated. Two or more filters
    together still need the raw Changeset table: no CA covers that shape
    (each one only tracks its own single dimension) — see TODO.md."""
    set_filters = [(n, v) for n, v in (('contributor', contributor), ('editor', editor), ('imagery', imagery), ('language', language)) if v]
    return set_filters[0] if len(set_filters) == 1 else None


# All six cross-dimension CAggs — "filter by one dimension, broken out by
# another" directly, the shape ToplistView's dimension param and
# TimeseriesView's group_by+filter both need and that no single-dimension
# CAgg can answer. Keyed by frozenset({dim_a, dim_b}) so a lookup works
# regardless of which side is the filter and which is the group/dimension.
# The editor/imagery/language pairs shipped first (migration 0041); the
# contributor pairs (migration 0043) were deferred initially — contributor
# has 344K distinct values vs. low hundreds for the other three, and every
# CAgg adds recurring refresh cost on this I/O-constrained host — then
# built once the contributor-grouped toplist was confirmed to be the
# remaining slow path. See docs/todo/continuous-aggregates-migration.md.
PAIR_CAGGS = {
    frozenset({'editor', 'imagery'}): CaggEditorImageryDaily,
    frozenset({'editor', 'language'}): CaggEditorLocaleDaily,
    frozenset({'imagery', 'language'}): CaggImageryLocaleDaily,
    frozenset({'contributor', 'editor'}): CaggContributorEditorDaily,
    frozenset({'contributor', 'imagery'}): CaggContributorImageryDaily,
    frozenset({'contributor', 'language'}): CaggContributorLocaleDaily,
}

# API dimension name -> the pair CAgg's own column name for it. Only
# 'language' differs (the CAgg columns follow this project's DB-ish naming,
# "locale", matching cagg_locale_daily — see CLAUDE.md's dimension-naming
# table for why the API param and the internal name are allowed to
# diverge).
_PAIR_CAGG_COLUMN = {'contributor': 'contributor', 'editor': 'editor', 'imagery': 'imagery', 'language': 'locale'}


def _pair_cagg_lookup(dim_a, dim_b):
    """(model, column_for_dim_a, column_for_dim_b) if a pair CAgg covers
    these two (distinct) dimensions, else None."""
    if dim_a == dim_b:
        return None
    model = PAIR_CAGGS.get(frozenset({dim_a, dim_b}))
    if model is None:
        return None
    return model, _PAIR_CAGG_COLUMN[dim_a], _PAIR_CAGG_COLUMN[dim_b]


def _resolve_range_and_filters(request):
    """start_date/end_date/contributor/editor/imagery/language, shared by
    TimeseriesView, SummaryView, and ToplistView — same query params,
    same "defaults to last 7 days" behaviour, across all three."""
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    if not start_date or not end_date:
        today = timezone.now().date()
        end_date = today.strftime('%Y-%m-%d')
        start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')

    contributor = request.query_params.get('contributor', '')
    editor = request.query_params.get('editor', '')
    imagery = request.query_params.get('imagery', '')
    language = request.query_params.get('language', '')
    filters = {
        'start_date': start_date, 'end_date': end_date,
        'contributor': contributor, 'editor': editor, 'imagery': imagery, 'language': language,
    }
    return start_date, end_date, contributor, editor, imagery, language, filters


def _filtered_changesets(start_date, end_date, contributor, editor, imagery, language):
    """Base queryset for the raw (filtered) path all three views fall back
    to when a contributor/editor/imagery/language filter is given — the
    rollup tables don't carry those dimensions. Half-open range on the raw
    datetime (not created_at__date) so the created_at index can be used
    directly — created_at__date forces a sequential scan since it's a
    function of the column, not the column itself."""
    changesets = Changeset.objects.all()
    end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
    changesets = changesets.filter(created_at__gte=start_date, created_at__lt=end_date_exclusive)
    if contributor:
        changesets = changesets.filter(user__iexact=contributor)
    if editor:
        changesets = changesets.filter(created_by_family__iexact=editor)
    if imagery:
        changesets = changesets.filter(imagery_family__isnull=True) if imagery == NONE_BUCKET else changesets.filter(imagery_family__iexact=imagery)
    if language:
        changesets = changesets.filter(locale_family__isnull=True) if language == NONE_BUCKET else changesets.filter(locale_family__iexact=language)
    return changesets


_FILTER_PARAMS = [
    OpenApiParameter('start_date', OpenApiTypes.DATE, description='Range start (YYYY-MM-DD). Defaults to 7 days before end_date.'),
    OpenApiParameter('end_date', OpenApiTypes.DATE, description='Range end (YYYY-MM-DD), inclusive. Defaults to today.'),
    OpenApiParameter('contributor', OpenApiTypes.STR, description='Restrict to one OSM username (case-insensitive).'),
    OpenApiParameter('editor', OpenApiTypes.STR, description='Restrict to one editor family (case-insensitive).'),
    OpenApiParameter('imagery', OpenApiTypes.STR, description='Restrict to one imagery family (case-insensitive).'),
    OpenApiParameter('language', OpenApiTypes.STR, description='Restrict to one language/locale, as recorded in the changeset (case-insensitive).'),
]


class TimeseriesView(APIView):
    """Changeset volume over time, optionally split into up to 20 named
    series via group_by. Bucket width (interval) is auto-picked to target
    ~300 points for the given range — hourly up to ~12.5 days, daily beyond
    that (see _pick_interval) — instead of a fixed grain per code path.
    Override with interval=hour|day; whichever grain is actually used is
    always reported back in the response's top-level `interval` field, so a
    caller never has to guess what they got.

    Backed by continuous aggregates (see migrations 0020/0023) when
    unfiltered, and also when exactly one of contributor/editor/imagery/
    language is set with no group_by (that dimension's own hourly-or-daily
    CA, filtered by name — same fast path as the grouped case). group_by
    combined with a filter is cross-dimension (e.g. "top editors, filtered
    by imagery") — backed by that pair's own daily CAgg (migrations 0041/
    0043, see PAIR_CAGGS above) whenever interval resolves to 'day' (all 6
    pair CAggs are daily-only, unlike the single-dimension ones — see
    docs/todo/continuous-aggregates-migration.md for why an hourly grain
    isn't built). interval=hour always falls back to the raw Changeset
    table for this shape — this still returns a correct `interval`, just
    without the CA-backed speed."""

    @extend_schema(
        tags=['changesets'],
        summary='Changeset volume over time',
        description=(
            'Time series of changeset volume, optionally split into up to 20 named series via '
            'group_by. Bucket width is auto-picked to target ~300 points for the given range '
            '(hourly for ranges up to ~12.5 days, daily beyond that) — override with '
            'interval=hour|day. interval=hour on a range wide enough to blow past the point '
            'budget is rejected (400) rather than silently truncated. The grain actually used '
            'is always reported back in the response\'s top-level interval field. Defaults to '
            'the last 7 days if no dates are given.'
        ),
        parameters=_FILTER_PARAMS + [
            OpenApiParameter('group_by', OpenApiTypes.STR, description='Split into per-name series: contributor, editor, imagery, or language. Omit for plain volume.'),
            OpenApiParameter('interval', OpenApiTypes.STR, description='Force the bucket width: hour or day. Omit to auto-pick based on range width (see description).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Narrow range — auto-picked hourly',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'group_by': None},
                'interval': 'hour', 'dates': ['2026-09-01 00:00'], 'series': [{'name': 'changesets', 'counts': [2100]}],
            },
            response_only=True,
        ), OpenApiExample(
            'Wide range — auto-picked daily',
            value={
                'filters': {'start_date': '2025-09-01', 'end_date': '2026-09-01', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'group_by': None},
                'interval': 'day', 'dates': ['2025-09-01'], 'series': [{'name': 'changesets', 'counts': [38700]}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        group_by = request.query_params.get('group_by') or None
        if group_by not in (None, *DIMENSION_FIELDS):
            return Response({'error': f'group_by must be one of: {", ".join(DIMENSION_FIELDS)}'}, status=status.HTTP_400_BAD_REQUEST)

        interval_param = request.query_params.get('interval') or None
        if interval_param not in (None, 'hour', 'day'):
            return Response({'error': "interval must be 'hour' or 'day'"}, status=status.HTTP_400_BAD_REQUEST)

        start_date, end_date, contributor, editor, imagery, language, filters = _resolve_range_and_filters(request)
        filters = {**filters, 'group_by': group_by}

        try:
            interval = _pick_interval(start_date, end_date, interval_param)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        single = _single_filter_dimension(contributor, editor, imagery, language)
        pair = _pair_cagg_lookup(single[0], group_by) if single and group_by is not None and interval == 'day' else None

        if not (contributor or editor or imagery or language):
            data = self._from_rollups(start_date, end_date, group_by, interval)
        elif single and group_by is None:
            # Plain volume-over-time for exactly one filtered dimension is
            # still a shape that dimension's own CA can answer directly (see
            # _single_filter_dimension) — same fast path as SummaryView's
            # scalar total, just not summed down to one number. A group_by
            # combined with a filter is still cross-dimension (e.g. "top
            # editors, filtered by imagery") and stays on the raw fallback.
            dimension, value = single
            data = self._from_rollups_single_filtered(start_date, end_date, dimension, value, interval)
        elif pair:
            data = self._from_pair_rollups(start_date, end_date, single[1], pair, group_by)
        else:
            data = self._from_raw(start_date, end_date, contributor, editor, imagery, language, group_by, interval)
        return Response({'filters': filters, **data})

    def _from_rollups_single_filtered(self, start_date, end_date, dimension, value, interval):
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        model = (CAGG_MODELS_HOURLY if interval == 'hour' else CAGG_MODELS)[dimension]
        rows = list(
            model.objects.filter(name__iexact=value, bucket__gte=start_date, bucket__lt=end_date_exclusive)
            .order_by('bucket')
        )
        dates = [_format_bucket(r.bucket, interval) for r in rows]
        return {'interval': interval, 'dates': dates, 'series': [{'name': 'changesets', 'counts': [r.cnt for r in rows]}]}

    def _from_pair_rollups(self, start_date, end_date, filter_value, pair, group_by):
        """`pair` is (model, filter_column, group_by_column) from
        _pair_cagg_lookup(filtered_dimension, group_by) — same top-20 +
        per-name-bucket pivot as _from_rollups' grouped branch, just against
        the pair CAgg's two columns instead of one. Daily-only (see the
        class docstring), so interval is always 'day' here."""
        model, filter_col, group_col = pair
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        filter_kwargs = {f'{filter_col}__iexact': filter_value}

        top = list(
            model.objects.filter(bucket__gte=start_date, bucket__lt=end_date_exclusive, **filter_kwargs)
            .values(group_col).annotate(total_count=Sum('cnt')).order_by('-total_count')[:20]
        )
        names = [row[group_col] for row in top]
        if not names:
            return {'interval': 'day', 'dates': [], 'series': []}

        rows = (
            model.objects.filter(
                bucket__gte=start_date, bucket__lt=end_date_exclusive,
                **{f'{group_col}__in': names}, **filter_kwargs,
            )
            .values('bucket', group_col, 'cnt')
        )
        per_name_bucket = defaultdict(dict)
        dates = set()
        for r in rows:
            label = _format_bucket(r['bucket'], 'day')
            per_name_bucket[r[group_col]][label] = r['cnt']
            dates.add(label)
        dates = sorted(dates)
        series = [{'name': name, 'counts': [per_name_bucket[name].get(d, 0) for d in dates]} for name in names]
        return {'interval': 'day', 'dates': dates, 'series': series}

    def _from_rollups(self, start_date, end_date, group_by, interval):
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)

        if group_by is None:
            volume = list(
                CAGG_VOLUME_MODELS[interval].objects.filter(bucket__gte=start_date, bucket__lt=end_date_exclusive)
                .order_by('bucket')
            )
            dates = [_format_bucket(v.bucket, interval) for v in volume]
            return {'interval': interval, 'dates': dates, 'series': [{'name': 'changesets', 'counts': [v.cnt for v in volume]}]}

        model = (CAGG_MODELS_HOURLY if interval == 'hour' else CAGG_MODELS).get(group_by)
        if model is None:
            return {'interval': interval, 'dates': [], 'series': []}

        top = list(
            model.objects.filter(bucket__gte=start_date, bucket__lt=end_date_exclusive)
            .values('name').annotate(total_count=Sum('cnt')).order_by('-total_count')[:20]
        )
        names = [row['name'] for row in top]
        if not names:
            return {'interval': interval, 'dates': [], 'series': []}

        rows = (
            model.objects.filter(name__in=names, bucket__gte=start_date, bucket__lt=end_date_exclusive)
            .values('bucket', 'name', 'cnt')
        )
        per_name_bucket = defaultdict(dict)
        dates = set()
        for r in rows:
            label = _format_bucket(r['bucket'], interval)
            per_name_bucket[r['name']][label] = r['cnt']
            dates.add(label)
        dates = sorted(dates)
        series = [{'name': name, 'counts': [per_name_bucket[name].get(d, 0) for d in dates]} for name in names]
        return {'interval': interval, 'dates': dates, 'series': series}

    def _from_raw(self, start_date, end_date, contributor, editor, imagery, language, group_by, interval):
        changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery, language)
        trunc = TruncHour('created_at') if interval == 'hour' else TruncDate('created_at')

        if group_by is None:
            rows = list(
                changesets.annotate(bucket=trunc)
                .values('bucket').annotate(count=Count('id')).order_by('bucket')
            )
            dates = [_format_bucket(r['bucket'], interval) for r in rows]
            return {'interval': interval, 'dates': dates, 'series': [{'name': 'changesets', 'counts': [r['count'] for r in rows]}]}

        field = DIMENSION_FIELDS[group_by]
        top = list(
            changesets.filter(**{f'{field}__isnull': False})
            .values(field).annotate(count=Count('id')).order_by('-count')[:20]
        )
        names = [row[field] for row in top]
        if not names:
            return {'interval': interval, 'dates': [], 'series': []}

        rows = (
            changesets.filter(**{f'{field}__in': names})
            .annotate(bucket=trunc)
            .values('bucket', field).annotate(count=Count('id'))
        )
        per_name_bucket = defaultdict(dict)
        dates = set()
        for r in rows:
            label = _format_bucket(r['bucket'], interval)
            per_name_bucket[r[field]][label] = r['count']
            dates.add(label)
        dates = sorted(dates)
        series = [{'name': name, 'counts': [per_name_bucket[name].get(d, 0) for d in dates]} for name in names]
        return {'interval': interval, 'dates': dates, 'series': series}


class SummaryView(APIView):
    """Single-number KPIs for a date range: total_changesets, total_objects
    (objects changed), avg_objects (objects per changeset). Backed by the
    cagg_volume_hourly continuous aggregate when unfiltered, or the matching
    per-dimension CA when exactly one of contributor/editor/imagery/language is
    set (it just needs a scalar total, which that CA already has
    pre-aggregated). Two or more filters together still fall back to the raw
    Changeset table — no CA covers that shape, each one only tracks its own
    dimension."""

    @extend_schema(
        tags=['changesets'],
        summary='Changeset summary totals',
        description='total_changesets, total_objects, and avg_objects for a date range. Defaults to the last 7 days if no dates are given.',
        parameters=_FILTER_PARAMS,
        responses={200: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Sample',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': ''},
                'total_changesets': 350000, 'total_objects': 12500000, 'avg_objects': 35.7,
            },
            response_only=True,
        )],
    )
    def get(self, request):
        start_date, end_date, contributor, editor, imagery, language, filters = _resolve_range_and_filters(request)
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        single = _single_filter_dimension(contributor, editor, imagery, language)

        if not (contributor or editor or imagery or language):
            totals = CaggVolumeHourly.objects.filter(bucket__gte=start_date, bucket__lt=end_date_exclusive).aggregate(
                total_changesets=Sum('cnt'), total_objects=Sum('changes_sum'))
        elif single:
            dimension, value = single
            totals = CAGG_MODELS[dimension].objects.filter(
                name__iexact=value, bucket__gte=start_date, bucket__lt=end_date_exclusive
            ).aggregate(total_changesets=Sum('cnt'), total_objects=Sum('changes_sum'))
        else:
            changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery, language)
            totals = changesets.aggregate(total_changesets=Count('id'), total_objects=Sum('changes_count'))

        total_changesets = totals['total_changesets'] or 0
        total_objects = totals['total_objects'] or 0
        avg_objects = round(total_objects / total_changesets, 1) if total_changesets else 0

        return Response({
            'filters': filters,
            'total_changesets': total_changesets,
            'total_objects': total_objects,
            'avg_objects': avg_objects,
        })


class ToplistView(APIView):
    """Top N by a metric, for one dimension (default N=20). Backed by that
    dimension's continuous aggregate when unfiltered. A single filter
    paired with a *different* dimension as `dimension` is backed by that
    pair's own CAgg (migrations 0041/0043 — see PAIR_CAGGS/
    _pair_cagg_lookup above and docs/todo/continuous-aggregates-
    migration.md) — all 6 dimension pairs are covered now. Anything else —
    2+ filters at once, or filter == dimension — falls back to the raw
    Changeset table; no CA tracks 2+ filter dimensions simultaneously, and
    filter == dimension is a degenerate case not worth its own CA lookup
    (see docs/todo/continuous-aggregates-migration.md)."""

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 1000

    @extend_schema(
        tags=['changesets'],
        summary='Top changesets by dimension',
        description=(
            'Top N (default 20) contributors/editors/imageries/locales, ranked by changeset '
            'count or by objects changed, for a date range. Defaults to the last 7 days if no '
            'dates are given.'
        ),
        parameters=_FILTER_PARAMS + [
            OpenApiParameter('dimension', OpenApiTypes.STR, required=True, description='One of: contributor, editor, imagery, language.'),
            OpenApiParameter('metric', OpenApiTypes.STR, description='count (changesets) or objects (objects changed). Defaults to count.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Number of results (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Top editors by count',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'dimension': 'editor', 'metric': 'count', 'limit': 20},
                'results': [{'name': 'iD', 'value': 180000}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        dimension = request.query_params.get('dimension', '')
        metric = request.query_params.get('metric', 'count')
        if dimension not in DIMENSION_FIELDS:
            return Response({'error': f'dimension must be one of: {", ".join(DIMENSION_FIELDS)}'}, status=status.HTTP_400_BAD_REQUEST)
        if metric not in ('count', 'objects'):
            return Response({'error': 'metric must be count or objects'}, status=status.HTTP_400_BAD_REQUEST)

        limit_raw = request.query_params.get('limit')
        if limit_raw is None:
            limit = self.DEFAULT_LIMIT
        else:
            try:
                limit = int(limit_raw)
            except ValueError:
                return Response({'error': 'limit must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
            if not (1 <= limit <= self.MAX_LIMIT):
                return Response({'error': f'limit must be between 1 and {self.MAX_LIMIT}'}, status=status.HTTP_400_BAD_REQUEST)

        start_date, end_date, contributor, editor, imagery, language, filters = _resolve_range_and_filters(request)
        filters = {**filters, 'dimension': dimension, 'metric': metric, 'limit': limit}

        single = _single_filter_dimension(contributor, editor, imagery, language)
        pair = _pair_cagg_lookup(single[0], dimension) if single else None

        if not (contributor or editor or imagery or language):
            results = self._from_rollups(start_date, end_date, dimension, metric, limit)
        elif pair:
            results = self._from_pair_rollups(start_date, end_date, single[1], pair, dimension, metric, limit)
        else:
            results = self._from_raw(start_date, end_date, contributor, editor, imagery, language, dimension, metric, limit)
        return Response({'filters': filters, 'results': results})

    def _from_rollups(self, start_date, end_date, dimension, metric, limit):
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        agg_field = 'cnt' if metric == 'count' else 'changes_sum'
        rows = (
            CAGG_MODELS[dimension].objects.filter(bucket__gte=start_date, bucket__lt=end_date_exclusive)
            .values('name').annotate(value=Sum(agg_field)).order_by('-value')[:limit]
        )
        return [{'name': r['name'], 'value': r['value']} for r in rows]

    def _from_pair_rollups(self, start_date, end_date, filter_value, pair, dimension, metric, limit):
        """`pair` is (model, filter_column, dimension_column) from
        _pair_cagg_lookup(filtered_dimension, dimension) — same shape as
        _from_rollups above, just filtered by the pair CAgg's other column
        first. iexact on the filter side matches _filtered_changesets'
        case-insensitivity for the same filter params."""
        model, filter_col, dimension_col = pair
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        agg_field = 'cnt' if metric == 'count' else 'changes_sum'
        rows = (
            model.objects.filter(
                bucket__gte=start_date, bucket__lt=end_date_exclusive,
                **{f'{filter_col}__iexact': filter_value},
            )
            .values(dimension_col).annotate(value=Sum(agg_field)).order_by('-value')[:limit]
        )
        return [{'name': r[dimension_col], 'value': r['value']} for r in rows]

    def _from_raw(self, start_date, end_date, contributor, editor, imagery, language, dimension, metric, limit):
        changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery, language)
        field = DIMENSION_FIELDS[dimension]
        agg = Count('id') if metric == 'count' else Sum('changes_count')
        rows = (
            changesets.filter(**{f'{field}__isnull': False})
            .values(field).annotate(value=agg).order_by('-value')[:limit]
        )
        return [{'name': r[field], 'value': r['value']} for r in rows]


class GeoView(APIView):
    """Changeset density per geohash cell (see changesets/geo.py), for the
    dashboard's map. Two resolutions, both backed by the single
    cagg_geo_hashed_daily CAgg (migration 0033) truncated to a different
    geohash prefix length (GEOHASH_PREFIX_LENGTH in geo.py) — a coarse cell
    is just a shorter prefix of a fine one, so one stored key serves every
    zoom level instead of choosing between two pre-materialized grids (the
    old cagg_geo_daily/cagg_geo_fine_daily design, retired in migration
    0038 — see CLAUDE.md's "Geo storage: a single geohash key" section):

    - resolution=coarse (default): global, ~156km x 156km cells.
    - resolution=fine: requires `bbox` (same "min_lon,min_lat,max_lon,max_lat"
      format as ChangesetQueryView's). Cell size adapts to the bbox itself
      (`geohash_precision_for_bbox()` in geo.py, up to ~1.2km x 0.6km at the
      finest stored precision) rather than one fixed size — a fixed fine
      size can't be right at every zoom level fine mode might be triggered
      at (see that function's docstring for the "looked blank until zooming
      much further" bug this replaced). Scoped to `bbox` even though
      pre-aggregated — shipping every fine cell on Earth would be excessive
      payload for one zoomed-in view.

    A contributor/editor/imagery/language filter falls back to the raw
    Changeset table (its own `geohash` column, migration 0032) — unlike
    ToplistView (see PAIR_CAGGS above), no CA covers a filtered geo
    breakdown here, deliberately not built yet: a dimension x geohash CAgg
    is a different shape from ToplistView's dimension x dimension pairs (geo
    cells aren't a small fixed set of names the way editor/imagery/language
    are), so it wasn't included in that same pass — see
    docs/todo/continuous-aggregates-migration.md. That fallback path's
    viewport scoping still uses the real, GiST-indexed `centroid` column
    (migration 0029) via the `&&` overlap operator, rather than decoding
    geohash back to lat/lon per row — cheaper, and centroid was already the
    source geohash was derived from.

    Cell coordinates (`lat`/`lon` in the response) are the geohash cell's
    *center*, decoded in Python from each returned cell's key — at most a
    few hundred rows per request, not a per-row database computation.

    Changesets whose bounding box is too large to trust (a stray far-away
    edited object can balloon an otherwise-local changeset's bbox — see
    changesets/geo.py) have no `centroid`/`geohash` and are excluded from
    `cells` at both resolutions, though they still count normally in
    SummaryView/TimeseriesView/ToplistView — so this endpoint's total can be
    slightly below those endpoints' totals for the same range.
    """

    @extend_schema(
        tags=['changesets'],
        summary='Changeset density by grid cell',
        description=(
            'Changeset count and objects-changed, bucketed into geohash-derived grid cells (see '
            '`lat_size_degrees`/`lon_size_degrees` in the response), for a date range. Defaults '
            'to the last 7 days if no dates are given. resolution=fine requires `bbox` (the '
            'viewport to scope cells to) and returns a finer grid than the default '
            'resolution=coarse. Changesets with an unreliably large bounding box (see the view '
            'docstring) are excluded from `cells`.'
        ),
        parameters=_FILTER_PARAMS + [
            OpenApiParameter('resolution', OpenApiTypes.STR, description='coarse (default, global) or fine (requires bbox below).'),
            OpenApiParameter('bbox', OpenApiTypes.STR, description='Viewport as "min_lon,min_lat,max_lon,max_lat". Required when resolution=fine.'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Grid cells for the default range',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': ''},
                'lat_size_degrees': 1.40625, 'lon_size_degrees': 1.40625,
                'cells': [{'lat': 51.5, 'lon': -0.5, 'count': 1234, 'objects': 45210}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        start_date, end_date, contributor, editor, imagery, language, filters = _resolve_range_and_filters(request)

        resolution = request.query_params.get('resolution', 'coarse')
        if resolution not in ('coarse', 'fine'):
            return Response({'error': "resolution must be 'coarse' or 'fine'"}, status=status.HTTP_400_BAD_REQUEST)

        bounds = None
        if resolution == 'fine':
            bbox = request.query_params.get('bbox')
            if not bbox:
                return Response({'error': 'bbox is required for resolution=fine'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                min_lon, min_lat, max_lon, max_lat = [float(v) for v in bbox.split(',')]
            except ValueError:
                return Response({'error': 'bbox must be min_lon,min_lat,max_lon,max_lat'}, status=status.HTTP_400_BAD_REQUEST)
            bounds = (min_lat, max_lat, min_lon, max_lon)

        if resolution == 'fine':
            # Adapts to the actual viewport size rather than one fixed
            # precision — see geohash_precision_for_bbox()'s docstring for
            # why a fixed fine precision looked blank until zooming well
            # past the point fine mode was supposed to already show detail.
            prefix_len = geohash_precision_for_bbox(min_lat, min_lon, max_lat, max_lon)
        else:
            prefix_len = GEOHASH_PREFIX_LENGTH[resolution]
        lat_size, lon_size = geohash_cell_size_degrees(prefix_len)

        if not (contributor or editor or imagery or language):
            cells = self._from_rollups(start_date, end_date, prefix_len, bounds)
        else:
            cells = self._from_raw(start_date, end_date, contributor, editor, imagery, language, prefix_len, bounds)
        return Response({
            'filters': filters,
            'lat_size_degrees': lat_size, 'lon_size_degrees': lon_size,
            'cells': cells,
        })

    def _from_rollups(self, start_date, end_date, prefix_len, bounds):
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        rows = CaggGeoHashedDaily.objects.filter(bucket__gte=start_date, bucket__lt=end_date_exclusive)
        if bounds:
            # The CAgg only stores the hashed key, not lat/lon or a real
            # geometry column, so it can't bbox-overlap like _from_raw does
            # against `centroid` — but a covering geohash prefix (the
            # longest common prefix of the bbox's SW/NE corners) still lets
            # this stay a sargable range scan instead of pulling every
            # geohash for the date range globally before cropping in Python.
            # Measured 2026-09-19: an unfiltered fine (viewport) request
            # with no SQL-level bbox filter at all scanned ~870K distinct
            # geohashes for a 1.5-month range — this prefix cuts that down
            # to whatever the covering cell actually contains.
            min_lat, max_lat, min_lon, max_lon = bounds
            prefix = geohash_bbox_prefix(min_lat, min_lon, max_lat, max_lon)
            if prefix:
                rows = rows.annotate(
                    in_prefix=RawSQL(geohash_prefix_range_sql(), [prefix, prefix + GEOHASH_PREFIX_UPPER_BOUND_CHAR]),
                ).filter(in_prefix=True)
        rows = (
            rows.annotate(cell=Substr('geohash', 1, prefix_len))
            .values('cell').annotate(count=Sum('cnt'), objects=Sum('changes_sum'))
        )
        cells = []
        for r in rows:
            lat, lon = geohash_decode_center(r['cell'])
            # The covering prefix above only guarantees containing the
            # bbox, not being tight to it, so the exact crop still has to
            # happen here in Python — same as before, just over far fewer
            # candidate rows now.
            if bounds:
                min_lat, max_lat, min_lon, max_lon = bounds
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue
            cells.append({'lat': lat, 'lon': lon, 'count': r['count'], 'objects': r['objects']})
        return cells

    def _from_raw(self, start_date, end_date, contributor, editor, imagery, language, prefix_len, bounds):
        # Sourced from the real `geohash` column (migration 0032) rather
        # than recomputing anything per row. Viewport scoping still goes
        # through the real, GiST-indexed `centroid` column (migration 0029)
        # via the `&&` overlap operator — cheaper than decoding geohash back
        # to lat/lon per row just to filter, and centroid is what geohash
        # was derived from in the first place.
        changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery, language)
        changesets = changesets.filter(geohash__isnull=False).annotate(cell=Substr('geohash', 1, prefix_len))
        if bounds:
            min_lat, max_lat, min_lon, max_lon = bounds
            changesets = changesets.annotate(
                in_viewport=RawSQL(viewport_overlap_sql(), [min_lon, min_lat, max_lon, max_lat]),
            ).filter(in_viewport=True)
        rows = changesets.values('cell').annotate(count=Count('id'), objects=Sum('changes_count'))
        cells = []
        for r in rows:
            lat, lon = geohash_decode_center(r['cell'])
            cells.append({'lat': lat, 'lon': lon, 'count': r['count'], 'objects': r['objects']})
        return cells


class BatchProgressView(APIView):
    @extend_schema(
        tags=['import'],
        summary='Live-poll batch progress',
        description='Progress of the poller\'s current catch-up batch (the live-sequence range it\'s working through), not the historical backfill.',
        responses={200: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Running', value={
                'running': True, 'last_sequence': 7172968, 'batch_start': 7172365,
                'batch_target': 7172980, 'done': 603, 'total': 615, 'pct': 98.0,
                'updated_at': '2026-09-08T02:54:10Z',
            },
            response_only=True,
        )],
    )
    def get(self, request):
        state = SequenceState.objects.first()
        if state is None:
            return Response({'running': False})

        batch_start  = state.batch_start
        batch_target = state.batch_target
        last         = state.last_sequence
        updated_at   = state.updated_at

        if batch_start is None or batch_target is None or batch_target <= batch_start:
            return Response({'running': False, 'last_sequence': last})

        total    = batch_target - batch_start
        done     = last - batch_start
        running  = last < batch_target

        return Response({
            'running':       running,
            'last_sequence': last,
            'batch_start':   batch_start,
            'batch_target':  batch_target,
            'done':          done,
            'total':         total,
            'pct':           round(done / total * 100, 1),
            'updated_at':    updated_at,
        })


class AutocompleteView(APIView):
    """Backed by FilterValue (see changesets.models / changesets.rollups),
    not the raw Changeset table — that table has one row per distinct
    contributor/editor/imagery value ever seen, globally deduplicated, so
    this stays fast regardless of how many changesets exist. Was previously
    a direct `Changeset.objects.filter(field__icontains=q).distinct()`
    query, which forced a full sequential scan of the whole table on every
    keystroke and stopped being usable once the table passed a few million
    rows."""

    @extend_schema(
        tags=['changesets'],
        summary='Autocomplete filter values',
        description='Up to 10 distinct known values for a filter field, matching a partial query — backs the dashboard\'s filter inputs.',
        parameters=[
            OpenApiParameter('field', OpenApiTypes.STR, required=True, description='One of: contributor, editor, imagery.'),
            OpenApiParameter('q', OpenApiTypes.STR, description='Partial value to match (case-insensitive, substring).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample('Sample', value=['StreetComplete', 'StreetComplete GO'], response_only=True)],
    )
    def get(self, request):
        field = request.query_params.get('field', '')
        q = request.query_params.get('q', '')
        if field not in dict(FilterValue.FIELD_CHOICES):
            return Response({'error': 'field must be contributor, editor, or imagery'}, status=status.HTTP_400_BAD_REQUEST)
        values = (
            FilterValue.objects
            .filter(field=field, value__icontains=q)
            .order_by('value')
            .values_list('value', flat=True)[:10]
        )
        return Response(list(values))


from django.views.generic import TemplateView

class APILandingPageView(TemplateView):
    """Poller status page — shows the poller's live catch-up batch progress
    (BatchProgressView/`/api/batch-progress/`, polled client-side). Used to
    also host a one-shot manual-import form; that form and its backing
    ChangesetListView/ImportJobView/ImportJob APIs were removed 2026-09-19
    — poll_sequences and import_from_dump are this project's only ingestion
    paths now (see CLAUDE.md)."""
    template_name = 'changesets/changesets.html'