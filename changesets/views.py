from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from django.db.models import Count, Value, CharField
from django.db.models.functions import TruncDate, ExtractHour, Concat
from .models import Changeset
from .serializers import ChangesetSerializer
from .osm_fetcher import fetch_and_process_changesets
from django.core.serializers.json import DjangoJSONEncoder
import json
from datetime import datetime, timedelta

class ChangesetListView(APIView):

    def get(self, request, seq_start, seq_end):
        seq_start = int(seq_start)
        seq_end = int(seq_end)
        
        max_range = 10000
        # Check if the range is too large
        if seq_end - seq_start > max_range:
            return Response(
                {"error": f"The range between seq_start and seq_end should not exceed {max_range}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch and process changesets
        min_changeset, max_changeset = fetch_and_process_changesets(seq_start, seq_end)

        # Instead of returning the data, just return a success message
        return Response({
            "message": f"Successfully processed changesets from sequence {seq_start} to {seq_end}",
            "min_changeset": min_changeset,
            "max_changeset": max_changeset
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
        
        # Base queryset
        changesets = Changeset.objects.all()
        
        # Apply date filters
        changesets = changesets.filter(created_at__gte=start_date)
        changesets = changesets.filter(created_at__lte=end_date)
        
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
        for contributor in top_contributors:
            user_data = changesets.filter(user=contributor['user']).annotate(
                date=TruncDate('created_at')
            ).values('date').annotate(
                count=Count('id')
            ).order_by('date')
            
            top_contributors_time.append({
                'name': contributor['user'],
                'counts': [item['count'] for item in user_data]
            })

        # Get time series data for top imageries - modified for SQLite
        top_imageries_time = []
        for imagery in top_imageries:
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
                if item['imagery_used'] and imagery['imagery_used'] in item['imagery_used']
            ]
            
            top_imageries_time.append({
                'name': imagery['imagery_used'],
                'counts': [item['count'] for item in filtered_data]
            })

        # Get time series data for top editors
        top_editors_time = []
        for editor in top_editors:
            editor_data = changesets.filter(created_by_family=editor['created_by_family']).annotate(
                date=TruncDate('created_at')
            ).values('date').annotate(
                count=Count('id')
            ).order_by('date')
            
            top_editors_time.append({
                'name': editor['created_by_family'],
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
        
        return context

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