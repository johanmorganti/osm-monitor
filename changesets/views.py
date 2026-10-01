"""HTML page shells. No database access: each page fetches its data
client-side from the JSON API (changesets/api/views.py)."""
from django.views.generic import TemplateView


class DashboardView(TemplateView):
    """Overview page: thin HTML shell, so it paints almost instantly; charts are
    filled by static/js/dashboard.js from the timeseries/summary/toplist/geo
    endpoints."""
    template_name = 'changesets/dashboard.html'


class EditorsView(TemplateView):
    """Editors page: one column per top-10 editor family for the selected range
    (default: last year), plus an "Other editor families" column, each with its
    own version drill-down toplist (dimension=editor_version) and volume graph.
    Family names aren't known at render time, so the columns are built
    client-side (static/js/editors.js)."""
    template_name = 'changesets/editors.html'


class APILandingPageView(TemplateView):
    """Poller status page: the poller's live catch-up batch progress
    (/api/batch-progress/, polled client-side)."""
    template_name = 'changesets/changesets.html'
