from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from django.db.models import Count, Value, CharField
from django.db.models.functions import TruncDate, ExtractHour, Concat
from .models import Changeset, SequenceState, ImportJob
from .serializers import ChangesetSerializer
from .osm_fetcher import fetch_and_process_changesets
from django.core.serializers.json import DjangoJSONEncoder
import json
import threading
from datetime import datetime, timedelta

class ChangesetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


class ChangesetQueryView(APIView):
    def get(self, request):
        qs = Changeset.objects.order_by('-created_at')

        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user = request.query_params.get('user')
        editor = request.query_params.get('editor')
        hashtag = request.query_params.get('hashtag')
        imagery = request.query_params.get('imagery')
        bbox = request.query_params.get('bbox')

        if start_date:
            qs = qs.filter(created_at__gte=start_date)
        if end_date:
            from datetime import date, timedelta
            end = datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1)
            qs = qs.filter(created_at__lt=end.strftime('%Y-%m-%d'))
        if user:
            qs = qs.filter(user=user)
        if editor:
            qs = qs.filter(created_by_family=editor)
        if hashtag:
            qs = qs.filter(hashtags__contains=[hashtag])
        if imagery:
            qs = qs.filter(imagery_used__contains=[imagery])
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
    template_name = 'changesets/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get date range from request parameters or use defaults
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')

        # If no dates provided, default to previous week
        if not start_date or not end_date:
            today = datetime.now().date()
            end_date = today.strftime('%Y-%m-%d')  # Today
            start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')  # 7 days ago (previous week)

        contributor = self.request.GET.get('contributor', '')
        editor      = self.request.GET.get('editor', '')
        imagery     = self.request.GET.get('imagery', '')

        # Base queryset
        changesets = Changeset.objects.all()

        # Apply date filters
        changesets = changesets.filter(created_at__gte=start_date)
        changesets = changesets.filter(created_at__lte=end_date)

        # Apply optional filters
        if contributor:
            changesets = changesets.filter(user__iexact=contributor)
        if editor:
            changesets = changesets.filter(created_by_family__iexact=editor)
        if imagery:
            changesets = changesets.filter(imagery_family__iexact=imagery)
        
        # Get daily changeset counts
        daily_counts = changesets.annotate(
            date=Concat(
                TruncDate('created_at'),
                Value(' '),
                ExtractHour('created_at'),
                Value(':00'),
                output_field=CharField()
            )
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')[:360]
        
        # Get top contributors
        top_contributors = Changeset.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date
        ).values('user').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        # Get top editors (by created_by_family)
        top_editors = Changeset.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            created_by_family__isnull=False
        ).values('created_by_family').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        # Get top imageries - modified for SQLite
        top_imageries = Changeset.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            imagery_used__isnull=False
        ).values('imagery_used').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        # Get time series data for top contributors
        top_contributors_time = []
        for contrib_item in top_contributors:
            user_data = changesets.filter(user=contrib_item['user']).annotate(
                date=TruncDate('created_at')
            ).values('date').annotate(
                count=Count('id')
            ).order_by('date')

            top_contributors_time.append({
                'name': contrib_item['user'],
                'counts': [item['count'] for item in user_data]
            })

        # Get time series data for top imageries - modified for SQLite
        top_imageries_time = []
        for imagery_item in top_imageries:
            # For SQLite, we'll need to process the JSON in Python
            imagery_data = changesets.filter(
                created_at__gte=start_date,
                created_at__lte=end_date,
                imagery_used__isnull=False
            ).annotate(
                date=TruncDate('created_at')
            ).values('date', 'imagery_used').annotate(
                count=Count('id')
            ).order_by('date')

            # Filter in Python for the specific imagery
            filtered_data = [
                item for item in imagery_data
                if item['imagery_used'] and imagery_item['imagery_used'] in item['imagery_used']
            ]

            top_imageries_time.append({
                'name': imagery_item['imagery_used'],
                'counts': [item['count'] for item in filtered_data]
            })

        # Get time series data for top editors
        top_editors_time = []
        for editor_item in top_editors:
            editor_data = changesets.filter(created_by_family=editor_item['created_by_family']).annotate(
                date=TruncDate('created_at')
            ).values('date').annotate(
                count=Count('id')
            ).order_by('date')

            top_editors_time.append({
                'name': editor_item['created_by_family'],
                'counts': [item['count'] for item in editor_data]
            })

        # Get unique dates for time series
        unique_dates = sorted(list(set(
            item['date'] for item in daily_counts
        )))

        # Convert dates to strings and properly serialize for JavaScript
        daily_counts_list = [
            {
                'date': item['date'],
                'count': item['count']
            }
            for item in daily_counts
        ]

        # Convert data to format suitable for JavaScript
        top_contributors_list = [
            {
                'user': item['user'],
                'count': item['count']
            }
            for item in top_contributors
        ]

        top_editors_list = [
            {
                'editor': item['created_by_family'],
                'count': item['count']
            }
            for item in top_editors
        ]

        top_imageries_list = [
            {
                'imagery': item['imagery_used'],
                'count': item['count']
            }
            for item in top_imageries
        ]
        
        context['daily_counts_json'] = json.dumps(daily_counts_list, cls=DjangoJSONEncoder)
        context['top_contributors_json'] = json.dumps(top_contributors_list, cls=DjangoJSONEncoder)
        context['top_editors_json'] = json.dumps(top_editors_list, cls=DjangoJSONEncoder)
        context['top_imageries_json'] = json.dumps(top_imageries_list, cls=DjangoJSONEncoder)
        
        # Add time series data - dates are already in the correct format from daily_counts
        context['top_contributors_time_json'] = json.dumps({
            'dates': unique_dates,
            'users': top_contributors_time
        }, cls=DjangoJSONEncoder)
        
        context['top_imageries_time_json'] = json.dumps({
            'dates': unique_dates,
            'imageries': top_imageries_time
        }, cls=DjangoJSONEncoder)
        
        context['top_editors_time_json'] = json.dumps({
            'dates': unique_dates,
            'editors': top_editors_time
        }, cls=DjangoJSONEncoder)
        
        context['total_changesets'] = changesets.count()
        context['start_date'] = start_date
        context['end_date'] = end_date
        context['contributor'] = contributor
        context['editor'] = editor
        context['imagery'] = imagery

        return context

class BatchProgressView(APIView):
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
    FIELD_MAP = {
        'contributor': ('user', 'user__icontains'),
        'editor':      ('created_by_family', 'created_by_family__icontains'),
        'imagery':     ('imagery_family', 'imagery_family__icontains'),
    }

    def get(self, request):
        field = request.query_params.get('field', '')
        q = request.query_params.get('q', '')
        if field not in self.FIELD_MAP:
            return Response({'error': 'field must be contributor, editor, or imagery'}, status=status.HTTP_400_BAD_REQUEST)
        db_field, lookup = self.FIELD_MAP[field]
        values = (
            Changeset.objects
            .filter(**{lookup: q})
            .exclude(**{f'{db_field}__isnull': True})
            .exclude(**{f'{db_field}__exact': ''})
            .values_list(db_field, flat=True)
            .distinct()[:10]
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
    return HttpResponseRedirect(reverse('api-landing-page'))