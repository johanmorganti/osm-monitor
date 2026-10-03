"""HTML page shells. No database access: each page fetches its data
client-side from the JSON API (changesets/api/views.py)."""
from django.views.generic import TemplateView


class DashboardView(TemplateView):
    """Overview page: thin HTML shell, so it paints almost instantly; charts are
    filled by static/js/dashboard.js from the timeseries/summary/toplist/geo
    endpoints."""
    template_name = 'changesets/dashboard.html'


class ObjectsView(TemplateView):
    """Objects page: how big changesets are (size distribution, percentiles,
    size by editor / day), the largest changesets by objects, and who and which campaigns change the most objects.
    Filled by static/js/objects.js from the distribution/largest/toplist/
    timeseries endpoints."""
    template_name = 'changesets/objects.html'


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
