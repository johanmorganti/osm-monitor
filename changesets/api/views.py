"""Public JSON API. Backend-agnostic: each view parses and validates its
parameters, asks the configured analytics backend (changesets/analytics/) and
shapes the response. How a question is answered (continuous aggregates, raw
scans, another database) is entirely the backend's business."""
from datetime import datetime, timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiTypes
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from ..analytics import DIMENSIONS, EDITOR_VERSION, HASHTAG, ChangesetQuery, backend_for
from ..geo import GEOHASH_PREFIX_LENGTH, geohash_cell_size_degrees, geohash_precision_for_bbox
from ..models import FilterValue, SequenceState
from ..serializers import ChangesetSerializer
from .params import resolve_filters, pick_interval, parse_bbox
from .schema import FILTER_PARAMS
from .sizes import distribution


class ChangesetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


class ChangesetQueryView(APIView):
    """Paginated raw changeset records, newest first, always date-bounded
    (default: last 24 hours). There's deliberately no unbounded "list
    everything" mode: at full-history scale both the scan and the COUNT(*)
    pagination needs would be prohibitively expensive; aggregate questions
    belong to timeseries/summary/toplist."""

    # Set so drf-spectacular documents the paginated envelope
    # (count/next/previous/results); pagination itself is done manually below.
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
        params = request.query_params
        start_date = params.get('start_date')
        end_date = params.get('end_date')

        # Explicit dates are whole-day boundaries; a missing one defaults off
        # the current instant, so "no dates" is a real rolling 24h window.
        now = timezone.now()
        end_dt = (datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)) if end_date else now
        start_dt = start_date if start_date else (now - timedelta(hours=24))

        bbox = None
        if params.get('bbox'):
            try:
                bbox = parse_bbox(params.get('bbox'))
            except (ValueError, TypeError):
                return Response({'error': 'bbox must be min_lon,min_lat,max_lon,max_lat'}, status=status.HTTP_400_BAD_REQUEST)

        query = ChangesetQuery(
            start=start_dt, end_exclusive=end_dt,
            user=params.get('user') or '', editor=params.get('editor') or '', hashtag=params.get('hashtag') or '',
            imagery_raw=params.get('imagery_raw') or '', imagery_family=params.get('imagery_family') or '',
            bbox=bbox,
        )
        paginator = ChangesetPagination()
        page = paginator.paginate_queryset(backend_for(request).changesets(query), request)
        return paginator.get_paginated_response(ChangesetSerializer(page, many=True).data)


class TimeseriesView(APIView):
    """Changeset volume over time, optionally split into up to 20 named series
    via group_by. The bucket width is auto-picked to target ~300 points (see
    params.pick_interval) unless interval= overrides it; the grain actually
    used is always reported back in `interval`."""

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
        parameters=FILTER_PARAMS + [
            OpenApiParameter('group_by', OpenApiTypes.STR, description='Split into per-name series: contributor, editor, imagery, language, country, or hashtag (lower-cased; a changeset counts under each of its hashtags). Omit for plain volume.'),
            OpenApiParameter('interval', OpenApiTypes.STR, description='Force the bucket width: hour or day. Omit to auto-pick based on range width (see description).'),
            OpenApiParameter('metric', OpenApiTypes.STR, description='count (changesets, default) or objects (objects changed). With group_by, also what the top 20 are ranked by.'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Narrow range — auto-picked hourly',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': '', 'group_by': None},
                'interval': 'hour', 'dates': ['2026-09-01 00:00'], 'series': [{'name': 'changesets', 'counts': [2100]}],
            },
            response_only=True,
        ), OpenApiExample(
            'Wide range — auto-picked daily',
            value={
                'filters': {'start_date': '2025-09-01', 'end_date': '2026-09-01', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': '', 'group_by': None},
                'interval': 'day', 'dates': ['2025-09-01'], 'series': [{'name': 'changesets', 'counts': [38700]}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        group_by = request.query_params.get('group_by') or None
        if group_by not in (None, *DIMENSIONS, HASHTAG):
            return Response({'error': f'group_by must be one of: {", ".join((*DIMENSIONS, HASHTAG))}'}, status=status.HTTP_400_BAD_REQUEST)

        metric = request.query_params.get('metric', 'count')
        if metric not in ('count', 'objects'):
            return Response({'error': 'metric must be count or objects'}, status=status.HTTP_400_BAD_REQUEST)

        interval_param = request.query_params.get('interval') or None
        if interval_param not in (None, 'hour', 'day'):
            return Response({'error': "interval must be 'hour' or 'day'"}, status=status.HTTP_400_BAD_REQUEST)

        f = resolve_filters(request)
        try:
            interval = pick_interval(f.start_date, f.end_date, interval_param)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        data = backend_for(request).timeseries(f, group_by, interval, metric)
        return Response({'filters': {**f.as_dict(), 'group_by': group_by, 'metric': metric}, **data})


class SummaryView(APIView):
    """Single-number KPIs for a date range: total_changesets, total_objects
    (objects changed), avg_objects (objects per changeset)."""

    @extend_schema(
        tags=['changesets'],
        summary='Changeset summary totals',
        description='total_changesets, total_objects, and avg_objects for a date range. Defaults to the last 7 days if no dates are given.',
        parameters=FILTER_PARAMS,
        responses={200: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Sample',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': ''},
                'total_changesets': 350000, 'total_objects': 12500000, 'avg_objects': 35.7,
            },
            response_only=True,
        )],
    )
    def get(self, request):
        f = resolve_filters(request)
        totals = backend_for(request).summary(f)
        total_changesets = totals['total_changesets'] or 0
        total_objects = totals['total_objects'] or 0
        avg_objects = round(total_objects / total_changesets, 1) if total_changesets else 0
        return Response({
            'filters': f.as_dict(),
            'total_changesets': total_changesets,
            'total_objects': total_objects,
            'avg_objects': avg_objects,
        })


class ToplistView(APIView):
    """Top N (default 20) values of one dimension by changeset count or objects
    changed. dimension=editor_version ranks exact editor versions within one
    editor family and requires an `editor` filter (created_by is unbounded
    across all editors)."""

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 1000

    @extend_schema(
        tags=['changesets'],
        summary='Top changesets by dimension',
        description=(
            'Top N (default 20) contributors/editors/imageries/locales/countries/editor-versions/hashtags, '
            'ranked by changeset count or by objects changed, for a date range. Defaults to the '
            'last 7 days if no dates are given. dimension=editor_version requires an editor filter '
            '(created_by is unbounded across all editors, so it is only ever grouped within one '
            'already-selected editor family).'
        ),
        parameters=FILTER_PARAMS + [
            OpenApiParameter('dimension', OpenApiTypes.STR, required=True, description='One of: contributor, editor, imagery, language, country, editor_version (requires an editor filter), hashtag (lower-cased; a changeset counts under each of its hashtags).'),
            OpenApiParameter('metric', OpenApiTypes.STR, description='count (changesets) or objects (objects changed). Defaults to count.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Number of results (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Top editors by count',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': '', 'dimension': 'editor', 'metric': 'count', 'limit': 20},
                'results': [{'name': 'iD', 'value': 180000}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        dimension = request.query_params.get('dimension', '')
        metric = request.query_params.get('metric', 'count')
        if dimension not in (*DIMENSIONS, EDITOR_VERSION, HASHTAG):
            valid = ', '.join((*DIMENSIONS, EDITOR_VERSION, HASHTAG))
            return Response({'error': f'dimension must be one of: {valid}'}, status=status.HTTP_400_BAD_REQUEST)
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

        f = resolve_filters(request)
        if dimension == EDITOR_VERSION and not f.editor:
            return Response({'error': 'dimension=editor_version requires an editor filter'}, status=status.HTTP_400_BAD_REQUEST)

        results = backend_for(request).toplist(f, dimension, metric, limit)
        return Response({'filters': {**f.as_dict(), 'dimension': dimension, 'metric': metric, 'limit': limit}, 'results': results})


class GeoView(APIView):
    """Changeset density per geohash cell, for the dashboard's map.

    - resolution=coarse (default): global, fixed precision
      (GEOHASH_PREFIX_LENGTH['coarse'], ~156km x 156km cells).
    - resolution=fine: requires `bbox`; the cell size adapts to the viewport
      (geohash_precision_for_bbox in geo.py) so detail stays legible at any
      zoom level fine mode triggers at.

    Cell coordinates are each cell's center. Changesets whose bounding box is
    too large to trust have no geohash and are left out of `cells` (while still
    counting in summary/timeseries/toplist), so this endpoint's total can be
    slightly below theirs for the same range.
    """

    @extend_schema(
        tags=['changesets'],
        summary='Changeset density by grid cell',
        description=(
            'Changeset count and objects-changed, bucketed into geohash-derived grid cells (see '
            '`lat_size_degrees`/`lon_size_degrees` in the response; each cell\'s `cell` is its geohash, '
            'usable with /api/changesets/geo/cell/), for a date range. Defaults '
            'to the last 7 days if no dates are given. resolution=fine requires `bbox` (the '
            'viewport to scope cells to) and returns a finer grid than the default '
            'resolution=coarse. Changesets with an unreliably large bounding box (see the view '
            'docstring) are excluded from `cells`.'
        ),
        parameters=FILTER_PARAMS + [
            OpenApiParameter('resolution', OpenApiTypes.STR, description='coarse (default, global) or fine (requires bbox below).'),
            OpenApiParameter('bbox', OpenApiTypes.STR, description='Viewport as "min_lon,min_lat,max_lon,max_lat". Required when resolution=fine.'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Grid cells for the default range',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': ''},
                'lat_size_degrees': 1.40625, 'lon_size_degrees': 1.40625,
                'cells': [{'cell': 'gcp', 'lat': 51.5, 'lon': -0.5, 'count': 1234, 'objects': 45210}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        f = resolve_filters(request)

        resolution = request.query_params.get('resolution', 'coarse')
        if resolution not in ('coarse', 'fine'):
            return Response({'error': "resolution must be 'coarse' or 'fine'"}, status=status.HTTP_400_BAD_REQUEST)

        bounds = None
        if resolution == 'fine':
            bbox = request.query_params.get('bbox')
            if not bbox:
                return Response({'error': 'bbox is required for resolution=fine'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
            except ValueError:
                return Response({'error': 'bbox must be min_lon,min_lat,max_lon,max_lat'}, status=status.HTTP_400_BAD_REQUEST)
            bounds = (min_lat, max_lat, min_lon, max_lon)
            prefix_len = geohash_precision_for_bbox(min_lat, min_lon, max_lat, max_lon)
        else:
            prefix_len = GEOHASH_PREFIX_LENGTH[resolution]
        lat_size, lon_size = geohash_cell_size_degrees(prefix_len)

        cells = backend_for(request).geo_cells(f, prefix_len, bounds)
        return Response({
            'filters': f.as_dict(),
            'lat_size_degrees': lat_size, 'lon_size_degrees': lon_size,
            'cells': cells,
        })


class DistributionView(APIView):
    """How changeset sizes (objects changed per changeset) are distributed:
    a roughly logarithmic histogram, exact percentiles, and how much of the
    objects total the largest 1% of changesets account for. Derived from
    exact per-size counts (sizes.distribution)."""

    @extend_schema(
        tags=['objects'],
        summary='Changeset size distribution',
        description=(
            'Histogram of changeset sizes (objects changed per changeset) in roughly logarithmic '
            'buckets, with changeset and object totals per bucket; exact p50/p90/p99/max sizes; and '
            '`top_1pct_objects_share`, the share of all objects changed by the largest 1% of '
            'changesets. Percentile = smallest size whose cumulative changeset count reaches that '
            'fraction. Defaults to the last 7 days if no dates are given.'
        ),
        parameters=FILTER_PARAMS,
        responses={200: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Sample',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': ''},
                'total_changesets': 350000, 'total_objects': 12500000, 'avg_objects': 35.7,
                'percentiles': {'p50': 5, 'p90': 132, 'p99': 1127, 'max': 10000},
                'top_1pct_objects_share': 0.4512,
                'buckets': [{'label': '2–4', 'min': 2, 'max': 4, 'changesets': 58000, 'objects': 160000}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        f = resolve_filters(request)
        return Response({'filters': f.as_dict(), **distribution(backend_for(request).size_counts(f))})


class SizeBreakdownView(APIView):
    """Changeset-size quartiles per group: the top N names of a dimension, or
    every day."""

    BY = (*DIMENSIONS, 'day')
    QUANTILES = (0.25, 0.5, 0.75, 0.9, 0.95)
    DEFAULT_LIMIT = 10
    MAX_LIMIT = 50

    @extend_schema(
        tags=['objects'],
        summary='Changeset size by group',
        description=(
            'Exact p25/p50/p75/p90/p95 changeset sizes (objects changed per changeset), plus changeset '
            'and object totals, per group. by=contributor|editor|imagery|language|country returns '
            'the top N names by changeset count (untagged changesets left out); by=day returns '
            'every day of the range, in order. Defaults to the last 7 days if no dates are given.'
        ),
        parameters=FILTER_PARAMS + [
            OpenApiParameter('by', OpenApiTypes.STR, required=True, description='One of: contributor, editor, imagery, language, country, day.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Number of names for a dimension (default {DEFAULT_LIMIT}, max {MAX_LIMIT}); ignored for day.'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Top editors',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': '', 'by': 'editor', 'limit': 10},
                'groups': [{'name': 'JOSM', 'changesets': 66000, 'objects': 9100000, 'avg_objects': 137.9, 'p25': 4, 'p50': 16, 'p75': 83, 'p90': 312, 'p95': 640}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        by = request.query_params.get('by', '')
        if by not in self.BY:
            return Response({'error': f'by must be one of: {", ".join(self.BY)}'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get('limit', self.DEFAULT_LIMIT))
        except ValueError:
            return Response({'error': 'limit must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        if not (1 <= limit <= self.MAX_LIMIT):
            return Response({'error': f'limit must be between 1 and {self.MAX_LIMIT}'}, status=status.HTTP_400_BAD_REQUEST)

        f = resolve_filters(request)
        groups = []
        for g in backend_for(request).size_quantiles(f, by, self.QUANTILES, limit):
            groups.append({
                'name': g['name'],
                'changesets': g['changesets'],
                'objects': g['objects'],
                'avg_objects': round(g['objects'] / g['changesets'], 1) if g['changesets'] else 0,
                **{f'p{round(level * 100)}': value for level, value in zip(self.QUANTILES, g['quantiles'])},
            })
        return Response({'filters': {**f.as_dict(), 'by': by, 'limit': limit}, 'groups': groups})


class LargestView(APIView):
    """The largest individual changesets of a range, by objects changed or by
    bounding-box area: mass edits, imports, bots, and edits that span
    continents (often a mistake, or a revert)."""

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    @extend_schema(
        tags=['objects'],
        summary='Largest changesets',
        description=(
            'The N largest changesets of the range, by=objects (objects changed, default) or '
            'by=area (bounding box area in km², on a spherical Earth; changesets without a bbox '
            'left out). Ties are broken newest first. Defaults to the last 7 days if no dates '
            'are given.'
        ),
        parameters=FILTER_PARAMS + [
            OpenApiParameter('by', OpenApiTypes.STR, description='objects (default) or area.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Number of changesets (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'By objects',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': '', 'by': 'objects', 'limit': 20},
                'results': [{'changeset_id': 172710690, 'created_at': '2026-09-03T10:12:00Z', 'user': 'someone', 'editor': 'JOSM',
                             'changes_count': 10000, 'area_km2': 12.4, 'country': 'FR', 'comment': 'Import buildings'}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        by = request.query_params.get('by', 'objects')
        if by not in ('objects', 'area'):
            return Response({'error': 'by must be objects or area'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get('limit', self.DEFAULT_LIMIT))
        except ValueError:
            return Response({'error': 'limit must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        if not (1 <= limit <= self.MAX_LIMIT):
            return Response({'error': f'limit must be between 1 and {self.MAX_LIMIT}'}, status=status.HTTP_400_BAD_REQUEST)

        f = resolve_filters(request)
        results = backend_for(request).largest(f, by, limit)
        return Response({'filters': {**f.as_dict(), 'by': by, 'limit': limit}, 'results': results})


class GeoCellView(APIView):
    """The changesets behind one map cell: those whose geohash starts with the
    cell's geohash (the `cell` of a /geo/ response), newest first, with the
    same filters and date range as the map."""

    GEOHASH_ALPHABET = set('0123456789bcdefghjkmnpqrstuvwxyz')
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 200

    @extend_schema(
        tags=['changesets'],
        summary='Changesets in a map cell',
        description=(
            'Changesets of one /api/changesets/geo/ cell (a changeset belongs to the cell containing '
            'its bounding-box center, like the map counts), newest first, paginated with '
            'limit/offset; `has_more` says whether another page exists. Same filters and date range '
            'as /geo/, so the cell\'s `count` there is this list\'s total. Defaults to the last 7 '
            'days if no dates are given.'
        ),
        parameters=FILTER_PARAMS + [
            OpenApiParameter('cell', OpenApiTypes.STR, required=True, description='The cell\'s geohash (1-12 characters), as returned in /geo/\'s `cell`.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Page size (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).'),
            OpenApiParameter('offset', OpenApiTypes.INT, description='Changesets to skip (default 0).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Sample',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'language': '', 'country': '', 'cell': 'u09', 'limit': 50, 'offset': 0},
                'has_more': True,
                'results': [{'changeset_id': 172710690, 'created_at': '2026-09-07T10:12:00Z', 'user': 'someone', 'editor': 'iD',
                             'changes_count': 12, 'area_km2': 0.4, 'country': 'FR', 'comment': 'Add shop'}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        cell = request.query_params.get('cell', '').lower()
        if not (1 <= len(cell) <= 12) or not set(cell) <= self.GEOHASH_ALPHABET:
            return Response({'error': 'cell must be a geohash of 1-12 characters'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get('limit', self.DEFAULT_LIMIT))
            offset = int(request.query_params.get('offset', 0))
        except ValueError:
            return Response({'error': 'limit and offset must be integers'}, status=status.HTTP_400_BAD_REQUEST)
        if not (1 <= limit <= self.MAX_LIMIT) or offset < 0:
            return Response({'error': f'limit must be between 1 and {self.MAX_LIMIT}, offset >= 0'}, status=status.HTTP_400_BAD_REQUEST)

        f = resolve_filters(request)
        # One extra row tells whether another page exists, without a count.
        rows = backend_for(request).cell_changesets(f, cell, limit + 1, offset)
        return Response({
            'filters': {**f.as_dict(), 'cell': cell, 'limit': limit, 'offset': offset},
            'has_more': len(rows) > limit,
            'results': rows[:limit],
        })


class BatchProgressView(APIView):
    """The poller's live catch-up progress. App state (SequenceState), not
    analytics, so it reads Postgres directly whatever the analytics backend."""

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

        batch_start = state.batch_start
        batch_target = state.batch_target
        last = state.last_sequence
        if batch_start is None or batch_target is None or batch_target <= batch_start:
            return Response({'running': False, 'last_sequence': last})

        total = batch_target - batch_start
        done = last - batch_start
        return Response({
            'running': last < batch_target,
            'last_sequence': last,
            'batch_start': batch_start,
            'batch_target': batch_target,
            'done': done,
            'total': total,
            'pct': round(done / total * 100, 1),
            'updated_at': state.updated_at,
        })


class AutocompleteView(APIView):
    """Known values of one filter field containing a partial query, for the
    dashboard's filter inputs."""

    @extend_schema(
        tags=['changesets'],
        summary='Autocomplete filter values',
        description='Up to 10 distinct known values for a filter field, matching a partial query — backs the dashboard\'s filter inputs.',
        parameters=[
            OpenApiParameter('field', OpenApiTypes.STR, required=True, description='One of: contributor, editor, imagery, language, country.'),
            OpenApiParameter('q', OpenApiTypes.STR, description='Partial value to match (case-insensitive, substring).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample('Sample', value=['StreetComplete', 'StreetComplete GO'], response_only=True)],
    )
    def get(self, request):
        field = request.query_params.get('field', '')
        q = request.query_params.get('q', '')
        if field not in dict(FilterValue.FIELD_CHOICES):
            valid = ', '.join(dict(FilterValue.FIELD_CHOICES))
            return Response({'error': f'field must be one of: {valid}'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(backend_for(request).autocomplete(field, q))
