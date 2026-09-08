from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from django.db.models import Count, Value, CharField, Sum, Avg
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
    of OSM history 100 rows at a time anyway. Use ChangesetStatsView for
    aggregate questions over a large range instead of pulling raw rows."""

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
            'are omitted (there\'s no unfiltered "everything" mode; use /api/changesets/stats/ for '
            'aggregate questions over a large range).'
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
    ChangesetStatsView so the page paints almost instantly; see dashboard.js."""
    template_name = 'changesets/dashboard.html'


class ChangesetStatsView(APIView):
    """Aggregated stats behind the dashboard's charts, as a standalone JSON API
    (same query params the dashboard UI uses: start_date, end_date, contributor,
    editor, imagery) — usable directly, not just by the dashboard's own JS.

    The unfiltered case (no contributor/editor/imagery — the common view,
    default 7-day window included) is served from the DailyVolume/DailyBreakdown
    rollup tables instead of scanning Changeset directly: those precompute
    per-day counts, so a request reads a few hundred pre-aggregated rows
    instead of hundreds of thousands of raw ones (see changesets/rollups.py).
    The rollup tables don't carry a contributor/editor/imagery dimension, so a
    request with any of those filters falls back to the raw Changeset query —
    filtered results are usually a small slice of the table anyway, so that
    path stays fast without needing a rollup per filter combination."""

    @extend_schema(
        tags=['changesets'],
        summary='Aggregated changeset stats',
        description=(
            'Daily volume, top editors/imageries/locales/contributors, and object-change '
            'totals over a date range. Defaults to the last 7 days if no dates are given.'
        ),
        parameters=[
            OpenApiParameter('start_date', OpenApiTypes.DATE, description='Range start (YYYY-MM-DD). Defaults to 7 days before end_date.'),
            OpenApiParameter('end_date', OpenApiTypes.DATE, description='Range end (YYYY-MM-DD), inclusive. Defaults to today.'),
            OpenApiParameter('contributor', OpenApiTypes.STR, description='Restrict to one OSM username (case-insensitive).'),
            OpenApiParameter('editor', OpenApiTypes.STR, description='Restrict to one editor family (case-insensitive).'),
            OpenApiParameter('imagery', OpenApiTypes.STR, description='Restrict to one imagery family (case-insensitive).'),
        ],
        responses={200: OpenApiTypes.OBJECT},
        examples=[OpenApiExample(
            'Unfiltered week',
            value={
                'filters': {'start_date': '2026-09-01', 'end_date': '2026-09-08', 'contributor': '', 'editor': '', 'imagery': ''},
                'total_changesets': 350000, 'total_objects': 12500000, 'avg_objects': 35.7,
                'daily_counts': [{'date': '2026-09-01 0:00', 'count': 2100}],
                'top_editors': [{'name': 'iD', 'count': 180000}],
                'top_editors_time': {'dates': ['2026-09-01 0:00'], 'series': [{'name': 'iD', 'counts': [1200]}]},
                'top_imageries': [{'name': 'Bing', 'count': 90000}],
                'top_imageries_time': {'dates': ['2026-09-01 0:00'], 'series': [{'name': 'Bing', 'counts': [600]}]},
                'top_locales': [{'name': 'EN', 'count': 200000}],
                'top_locales_time': {'dates': ['2026-09-01 0:00'], 'series': [{'name': 'EN', 'counts': [1400]}]},
                'top_contributors_by_objects': [{'name': 'someuser', 'total': 50000}],
                'top_editors_by_objects': [{'name': 'iD', 'total': 6000000}],
            },
            response_only=True,
        )],
    )
    def get(self, request):
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # If no dates provided, default to previous week
        if not start_date or not end_date:
            today = datetime.now().date()
            end_date = today.strftime('%Y-%m-%d')  # Today
            start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')  # 7 days ago (previous week)

        contributor = request.query_params.get('contributor', '')
        editor      = request.query_params.get('editor', '')
        imagery     = request.query_params.get('imagery', '')

        filters = {
            'start_date': start_date, 'end_date': end_date,
            'contributor': contributor, 'editor': editor, 'imagery': imagery,
        }

        if not (contributor or editor or imagery):
            return Response(self._from_rollups(start_date, end_date, filters))
        return Response(self._from_raw(start_date, end_date, contributor, editor, imagery, filters))

    def _from_rollups(self, start_date, end_date, filters):
        volume = list(
            DailyVolume.objects.filter(date__gte=start_date, date__lte=end_date)
            .order_by('date', 'hour')[:360]
        )
        daily_counts = [{'date': f'{v.date.isoformat()} {v.hour}:00', 'count': v.count} for v in volume]
        unique_dates = [d['date'] for d in daily_counts]

        total_changesets = sum(v.count for v in volume)
        total_objects = sum(v.changes_sum for v in volume)
        avg_objects = round(total_objects / total_changesets, 1) if total_changesets else 0

        def top_with_time(category):
            top = list(
                DailyBreakdown.objects.filter(category=category, date__gte=start_date, date__lte=end_date)
                .values('name').annotate(total_count=Sum('count')).order_by('-total_count')[:20]
            )
            names = [row['name'] for row in top]
            if not names:
                return [], []

            rows = (
                DailyBreakdown.objects.filter(
                    category=category, name__in=names, date__gte=start_date, date__lte=end_date
                ).values('date', 'name', 'count')
            )
            per_name_day = defaultdict(dict)
            for r in rows:
                per_name_day[r['name']][r['date'].isoformat()] = r['count']

            top_list = [{'name': row['name'], 'count': row['total_count']} for row in top]
            time_series = [
                {
                    'name': name,
                    'counts': [per_name_day[name].get(d.split(' ')[0], 0) for d in unique_dates],
                }
                for name in names
            ]
            return top_list, time_series

        top_editors, top_editors_time = top_with_time('editor')
        top_imageries, top_imageries_time = top_with_time('imagery')
        top_locales, top_locales_time = top_with_time('locale')

        top_contributors_by_objects = list(
            DailyBreakdown.objects.filter(category='contributor', date__gte=start_date, date__lte=end_date)
            .values('name').annotate(total=Sum('changes_sum')).order_by('-total')[:20]
        )
        top_editors_by_objects = list(
            DailyBreakdown.objects.filter(category='editor', date__gte=start_date, date__lte=end_date)
            .values('name').annotate(total=Sum('changes_sum')).order_by('-total')[:20]
        )

        return {
            'filters': filters,
            'total_changesets': total_changesets,
            'total_objects': total_objects,
            'avg_objects': avg_objects,
            'daily_counts': daily_counts,
            'top_editors': top_editors,
            'top_editors_time': {'dates': unique_dates, 'series': top_editors_time},
            'top_imageries': top_imageries,
            'top_imageries_time': {'dates': unique_dates, 'series': top_imageries_time},
            'top_locales': top_locales,
            'top_locales_time': {'dates': unique_dates, 'series': top_locales_time},
            'top_contributors_by_objects': [{'name': r['name'], 'total': r['total']} for r in top_contributors_by_objects],
            'top_editors_by_objects': [{'name': r['name'], 'total': r['total']} for r in top_editors_by_objects],
        }

    def _from_raw(self, start_date, end_date, contributor, editor, imagery, filters):
        # Base queryset
        changesets = Changeset.objects.all()

        # Half-open range on the raw datetime (not created_at__date) so the
        # created_at index can be used directly — created_at__date forces a
        # sequential scan since it's a function of the column, not the column
        # itself. end_date_exclusive makes end_date inclusive of its whole day.
        end_date_exclusive = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
        changesets = changesets.filter(created_at__gte=start_date)
        changesets = changesets.filter(created_at__lt=end_date_exclusive)

        # Apply optional filters
        if contributor:
            changesets = changesets.filter(user__iexact=contributor)
        if editor:
            changesets = changesets.filter(created_by_family__iexact=editor)
        if imagery:
            changesets = changesets.filter(imagery_family__iexact=imagery)

        # ── Part 1: Changeset activity ────────────────────────────────────────

        daily_counts = changesets.annotate(
            date=Concat(
                TruncDate('created_at'),
                Value(' '),
                ExtractHour('created_at'),
                Value(':00'),
                output_field=CharField()
            )
        ).values('date').annotate(count=Count('id')).order_by('date')[:360]

        unique_dates = sorted({item['date'] for item in daily_counts})

        def top_with_time(field):
            """Top 20 by count, plus each one's daily series aligned to
            unique_dates — as 2 queries total regardless of how many names
            there are, instead of 1 per name (was up to 60 queries across the
            three categories on top of everything else)."""
            top = list(
                changesets.filter(**{f'{field}__isnull': False})
                .values(field).annotate(count=Count('id'))
                .order_by('-count')[:20]
            )
            names = [row[field] for row in top]
            if not names:
                return [], []

            rows = (
                changesets.filter(**{f'{field}__in': names})
                .annotate(date=TruncDate('created_at'))
                .values('date', field).annotate(count=Count('id'))
            )
            per_name_day = defaultdict(dict)
            for r in rows:
                day = r['date'].isoformat() if hasattr(r['date'], 'isoformat') else str(r['date'])
                per_name_day[r[field]][day] = r['count']

            top_list = [{'name': row[field], 'count': row['count']} for row in top]
            time_series = [
                {
                    'name': name,
                    'counts': [per_name_day[name].get(d.split(' ')[0] if ' ' in d else d, 0) for d in unique_dates],
                }
                for name in names
            ]
            return top_list, time_series

        top_editors, top_editors_time = top_with_time('created_by_family')
        top_imageries, top_imageries_time = top_with_time('imagery_family')
        top_locales, top_locales_time = top_with_time('locale_family')

        # ── Part 2: Objects changed ───────────────────────────────────────────

        totals = changesets.aggregate(total=Sum('changes_count'), avg=Avg('changes_count'))
        total_objects = totals['total'] or 0
        avg_objects   = round(totals['avg'] or 0, 1)

        top_contributors_by_objects = list(
            changesets.filter(user__isnull=False)
            .values('user').annotate(total=Sum('changes_count'))
            .order_by('-total')[:20]
        )
        top_editors_by_objects = list(
            changesets.filter(created_by_family__isnull=False)
            .values('created_by_family').annotate(total=Sum('changes_count'))
            .order_by('-total')[:20]
        )

        # ── Response ─────────────────────────────────────────────────────────

        return {
            'filters': filters,
            'total_changesets': changesets.count(),
            'total_objects': total_objects,
            'avg_objects': avg_objects,
            'daily_counts': [{'date': i['date'], 'count': i['count']} for i in daily_counts],
            'top_editors': top_editors,
            'top_editors_time': {'dates': unique_dates, 'series': top_editors_time},
            'top_imageries': top_imageries,
            'top_imageries_time': {'dates': unique_dates, 'series': top_imageries_time},
            'top_locales': top_locales,
            'top_locales_time': {'dates': unique_dates, 'series': top_locales_time},
            'top_contributors_by_objects': [{'name': r['user'], 'total': r['total']} for r in top_contributors_by_objects],
            'top_editors_by_objects': [{'name': r['created_by_family'], 'total': r['total']} for r in top_editors_by_objects],
        }

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