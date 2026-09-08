from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from django.db.models import Count, Value, CharField, Sum
from django.db.models.functions import TruncDate, ExtractHour, Concat
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiTypes
from .models import Changeset, SequenceState, ImportJob, DailyVolume, DailyBreakdown, FilterValue
from .serializers import ChangesetSerializer
from .osm_fetcher import fetch_and_process_changesets
import threading
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


class ChangesetDetailView(APIView):
    @extend_schema(
        tags=['changesets'],
        summary='Get one changeset',
        description='A single changeset record by its OSM changeset ID.',
        responses=ChangesetSerializer,
    )
    def get(self, request, changeset_id):
        changeset = get_object_or_404(Changeset, changeset_id=changeset_id)
        serializer = ChangesetSerializer(changeset)
        return Response(serializer.data)


def _run_import_job(job_id, seq_start, seq_end):
    import django
    django.setup()
    job = ImportJob.objects.get(id=job_id)
    job.status = 'running'
    job.save(update_fields=['status'])
    try:
        def on_progress(seq):
            ImportJob.objects.filter(id=job_id).update(current_seq=seq)

        fetch_and_process_changesets(seq_start, seq_end, on_progress=on_progress)

        job.status = 'done'
        job.current_seq = seq_end
        job.save(update_fields=['status', 'current_seq'])
    except Exception as e:
        ImportJob.objects.filter(id=job_id).update(status='error', error=str(e))


class ChangesetListView(APIView):
    """Kicks off a one-shot background import of a replication sequence
    range and returns a job to poll via ImportJobView — not itself the
    changeset data. See poll_sequences (manage.py) for continuous ingestion."""

    @extend_schema(
        tags=['import'],
        summary='Import a sequence range',
        description=(
            'Starts a background import of OSM replication sequences seq_start..seq_end '
            '(inclusive), capped at 10000 sequences per call. Returns a job to poll via '
            'GET /api/import-job/{job_id}/.'
        ),
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Job started', value={'job_id': 42, 'seq_start': 1000, 'seq_end': 1010, 'total': 11},
            response_only=True,
        )],
    )
    def get(self, request, seq_start, seq_end):
        seq_start = int(seq_start)
        seq_end = int(seq_end)

        max_range = 10000
        if seq_end - seq_start > max_range:
            return Response(
                {"error": f"The range between seq_start and seq_end should not exceed {max_range}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        total = abs(seq_end - seq_start) + 1
        job = ImportJob.objects.create(seq_start=seq_start, seq_end=seq_end, total=total)

        t = threading.Thread(target=_run_import_job, args=(job.id, seq_start, seq_end), daemon=True)
        t.start()

        return Response({"job_id": job.id, "seq_start": seq_start, "seq_end": seq_end, "total": total})


class ImportJobView(APIView):
    @extend_schema(
        tags=['import'],
        summary='Import job status',
        description='Progress of a background sequence-range import started via GET /api/sequence/{seq_start}/{seq_end}/.',
        responses={200: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'In progress',
            value={
                'job_id': 42, 'status': 'running', 'seq_start': 1000, 'seq_end': 1010,
                'current_seq': 1004, 'total': 11, 'done': 5, 'pct': 45.5, 'error': None,
            },
            response_only=True,
        )],
    )
    def get(self, request, job_id):
        job = ImportJob.objects.filter(id=job_id).first()
        if job is None:
            return Response({'error': 'Job not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'job_id':      job.id,
            'status':      job.status,
            'seq_start':   job.seq_start,
            'seq_end':     job.seq_end,
            'current_seq': job.current_seq,
            'total':       job.total,
            'done':        job.done,
            'pct':         job.pct,
            'error':       job.error,
        })

class DashboardView(TemplateView):
    """Thin HTML shell — no DB access. Chart data is fetched client-side from
    TimeseriesView/SummaryView/ToplistView so the page paints almost
    instantly; see dashboard.js."""
    template_name = 'changesets/dashboard.html'


# Dimension name (as used in the public API and in DailyBreakdown.category)
# -> the raw Changeset field it corresponds to. Shared by TimeseriesView's
# group_by and ToplistView's dimension.
DIMENSION_FIELDS = {
    'contributor': 'user',
    'editor': 'created_by_family',
    'imagery': 'imagery_family',
    'locale': 'locale_family',
}


def _resolve_range_and_filters(request):
    """start_date/end_date/contributor/editor/imagery, shared by
    TimeseriesView, SummaryView, and ToplistView — same query params,
    same "defaults to last 7 days" behaviour, across all three."""
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    if not start_date or not end_date:
        today = datetime.now().date()
        end_date = today.strftime('%Y-%m-%d')
        start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')

    contributor = request.query_params.get('contributor', '')
    editor = request.query_params.get('editor', '')
    imagery = request.query_params.get('imagery', '')
    filters = {
        'start_date': start_date, 'end_date': end_date,
        'contributor': contributor, 'editor': editor, 'imagery': imagery,
    }
    return start_date, end_date, contributor, editor, imagery, filters


def _filtered_changesets(start_date, end_date, contributor, editor, imagery):
    """Base queryset for the raw (filtered) path all three views fall back
    to when a contributor/editor/imagery filter is given — the rollup
    tables don't carry those dimensions. Half-open range on the raw
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
        changesets = changesets.filter(imagery_family__iexact=imagery)
    return changesets


_FILTER_PARAMS = [
    OpenApiParameter('start_date', OpenApiTypes.DATE, description='Range start (YYYY-MM-DD). Defaults to 7 days before end_date.'),
    OpenApiParameter('end_date', OpenApiTypes.DATE, description='Range end (YYYY-MM-DD), inclusive. Defaults to today.'),
    OpenApiParameter('contributor', OpenApiTypes.STR, description='Restrict to one OSM username (case-insensitive).'),
    OpenApiParameter('editor', OpenApiTypes.STR, description='Restrict to one editor family (case-insensitive).'),
    OpenApiParameter('imagery', OpenApiTypes.STR, description='Restrict to one imagery family (case-insensitive).'),
]


class TimeseriesView(APIView):
    """Changeset volume over time. Without group_by: plain hourly volume.
    With group_by: daily volume split into up to 20 series — the top names
    by total count over the range (the rollup/raw split below can't offer
    hourly granularity once grouped, since DailyBreakdown only has a daily
    grain — there's no hourly equivalent to fall back to).

    Backed by the DailyVolume/DailyBreakdown rollup tables when unfiltered;
    a contributor/editor/imagery filter falls back to the raw Changeset
    table, since the rollups don't carry those dimensions (see
    changesets/rollups.py)."""

    @extend_schema(
        tags=['changesets'],
        summary='Changeset volume over time',
        description=(
            'Time series of changeset volume. Without group_by: hourly, unfiltered range. '
            'With group_by: daily, split into up to 20 series (the top names by total count '
            'over the range). Defaults to the last 7 days if no dates are given.'
        ),
        parameters=_FILTER_PARAMS + [
            OpenApiParameter('group_by', OpenApiTypes.STR, description='Split into per-name series: contributor, editor, imagery, or locale. Omit for plain hourly volume.'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Plain hourly volume',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'group_by': None},
                'dates': ['2026-09-01 0:00'], 'series': [{'name': 'changesets', 'counts': [2100]}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        group_by = request.query_params.get('group_by') or None
        if group_by not in (None, *DIMENSION_FIELDS):
            return Response({'error': f'group_by must be one of: {", ".join(DIMENSION_FIELDS)}'}, status=status.HTTP_400_BAD_REQUEST)

        start_date, end_date, contributor, editor, imagery, filters = _resolve_range_and_filters(request)
        filters = {**filters, 'group_by': group_by}

        if not (contributor or editor or imagery):
            data = self._from_rollups(start_date, end_date, group_by)
        else:
            data = self._from_raw(start_date, end_date, contributor, editor, imagery, group_by)
        return Response({'filters': filters, **data})

    def _from_rollups(self, start_date, end_date, group_by):
        if group_by is None:
            volume = list(
                DailyVolume.objects.filter(date__gte=start_date, date__lte=end_date)
                .order_by('date', 'hour')[:360]
            )
            dates = [f'{v.date.isoformat()} {v.hour}:00' for v in volume]
            return {'dates': dates, 'series': [{'name': 'changesets', 'counts': [v.count for v in volume]}]}

        top = list(
            DailyBreakdown.objects.filter(category=group_by, date__gte=start_date, date__lte=end_date)
            .values('name').annotate(total_count=Sum('count')).order_by('-total_count')[:20]
        )
        names = [row['name'] for row in top]
        if not names:
            return {'dates': [], 'series': []}

        rows = (
            DailyBreakdown.objects.filter(category=group_by, name__in=names, date__gte=start_date, date__lte=end_date)
            .values('date', 'name', 'count')
        )
        per_name_day = defaultdict(dict)
        dates = set()
        for r in rows:
            day = r['date'].isoformat()
            per_name_day[r['name']][day] = r['count']
            dates.add(day)
        dates = sorted(dates)
        series = [{'name': name, 'counts': [per_name_day[name].get(d, 0) for d in dates]} for name in names]
        return {'dates': dates, 'series': series}

    def _from_raw(self, start_date, end_date, contributor, editor, imagery, group_by):
        changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery)

        if group_by is None:
            rows = list(
                changesets.annotate(
                    date=Concat(TruncDate('created_at'), Value(' '), ExtractHour('created_at'), Value(':00'), output_field=CharField())
                ).values('date').annotate(count=Count('id')).order_by('date')[:360]
            )
            return {'dates': [r['date'] for r in rows], 'series': [{'name': 'changesets', 'counts': [r['count'] for r in rows]}]}

        field = DIMENSION_FIELDS[group_by]
        top = list(
            changesets.filter(**{f'{field}__isnull': False})
            .values(field).annotate(count=Count('id')).order_by('-count')[:20]
        )
        names = [row[field] for row in top]
        if not names:
            return {'dates': [], 'series': []}

        rows = (
            changesets.filter(**{f'{field}__in': names})
            .annotate(date=TruncDate('created_at'))
            .values('date', field).annotate(count=Count('id'))
        )
        per_name_day = defaultdict(dict)
        dates = set()
        for r in rows:
            day = r['date'].isoformat()
            per_name_day[r[field]][day] = r['count']
            dates.add(day)
        dates = sorted(dates)
        series = [{'name': name, 'counts': [per_name_day[name].get(d, 0) for d in dates]} for name in names]
        return {'dates': dates, 'series': series}


class SummaryView(APIView):
    """Single-number KPIs for a date range: total_changesets, total_objects
    (objects changed), avg_objects (objects per changeset). Backed by the
    DailyVolume rollup table when unfiltered; a contributor/editor/imagery
    filter falls back to the raw Changeset table."""

    @extend_schema(
        tags=['changesets'],
        summary='Changeset summary totals',
        description='total_changesets, total_objects, and avg_objects for a date range. Defaults to the last 7 days if no dates are given.',
        parameters=_FILTER_PARAMS,
        responses={200: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Sample',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': ''},
                'total_changesets': 350000, 'total_objects': 12500000, 'avg_objects': 35.7,
            },
            response_only=True,
        )],
    )
    def get(self, request):
        start_date, end_date, contributor, editor, imagery, filters = _resolve_range_and_filters(request)

        if not (contributor or editor or imagery):
            totals = DailyVolume.objects.filter(date__gte=start_date, date__lte=end_date).aggregate(
                total_changesets=Sum('count'), total_objects=Sum('changes_sum'))
        else:
            changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery)
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
    """Top N by a metric, for one dimension (default N=20). Backed by the
    DailyBreakdown rollup table when unfiltered; a contributor/editor/
    imagery filter falls back to the raw Changeset table."""

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
            OpenApiParameter('dimension', OpenApiTypes.STR, required=True, description='One of: contributor, editor, imagery, locale.'),
            OpenApiParameter('metric', OpenApiTypes.STR, description='count (changesets) or objects (objects changed). Defaults to count.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Number of results (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Top editors by count',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': '', 'dimension': 'editor', 'metric': 'count', 'limit': 20},
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

        start_date, end_date, contributor, editor, imagery, filters = _resolve_range_and_filters(request)
        filters = {**filters, 'dimension': dimension, 'metric': metric, 'limit': limit}

        if not (contributor or editor or imagery):
            results = self._from_rollups(start_date, end_date, dimension, metric, limit)
        else:
            results = self._from_raw(start_date, end_date, contributor, editor, imagery, dimension, metric, limit)
        return Response({'filters': filters, 'results': results})

    def _from_rollups(self, start_date, end_date, dimension, metric, limit):
        agg_field = 'count' if metric == 'count' else 'changes_sum'
        rows = (
            DailyBreakdown.objects.filter(category=dimension, date__gte=start_date, date__lte=end_date)
            .values('name').annotate(value=Sum(agg_field)).order_by('-value')[:limit]
        )
        return [{'name': r['name'], 'value': r['value']} for r in rows]

    def _from_raw(self, start_date, end_date, contributor, editor, imagery, dimension, metric, limit):
        changesets = _filtered_changesets(start_date, end_date, contributor, editor, imagery)
        field = DIMENSION_FIELDS[dimension]
        agg = Count('id') if metric == 'count' else Sum('changes_count')
        rows = (
            changesets.filter(**{f'{field}__isnull': False})
            .values(field).annotate(value=agg).order_by('-value')[:limit]
        )
        return [{'name': r[field], 'value': r['value']} for r in rows]

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
    template_name = 'changesets/changesets.html'

    def get_context_data(self, **kwargs):
        import yaml, requests
        context = super().get_context_data(**kwargs)
        context['last_changeset_id'] = int(yaml.load(requests.get("https://planet.osm.org/replication/changesets/state.yaml", stream=True).raw.read(),Loader=yaml.FullLoader)["sequence"])
        return context

## Redirect to landing page
from django.http import HttpResponseRedirect
from django.urls import reverse

def redirect_to_landing_page(request):
    return HttpResponseRedirect(reverse('changeset-import'))