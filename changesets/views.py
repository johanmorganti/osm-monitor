"""HTML page shells. No database access: each page fetches its data
client-side from the JSON API (changesets/api/views.py)."""
from django.templatetags.static import static
from django.views.generic import TemplateView
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularSwaggerView

from .mcp.tools import public_tools

# Swagger UI is served from static/vendor/ (see its README.md), not drf-spectacular's default
# CDN URL (swagger-ui-dist@latest on jsDelivr: unpinned, so any new release ran on the page).
SWAGGER_UI_VERSION_DIR = 'vendor/swagger-ui-dist-5.33.2'


class DashboardView(TemplateView):
    """Overview page: thin HTML shell, so it paints almost instantly; charts are
    filled by static/js/dashboard.js from the timeseries/summary/toplist/geo
    endpoints."""
    template_name = 'changesets/dashboard.html'


class ObjectsView(TemplateView):
    """Objects page: how big changesets are (size distribution, percentiles,
    size by editor / day), what was changed (action, object type, feature, from
    the replication diffs), the largest changesets by objects, and who and which campaigns change the most objects.
    Filled by static/js/objects.js from the distribution/largest/toplist/
    timeseries endpoints."""
    template_name = 'changesets/objects.html'


class EditorsView(TemplateView):
    """Editors page: one column per family of a fixed list (TOP_FAMILIES in editors.js, the
    same for any range; default range: last year), plus an "Other editor families" column, each with its
    own version drill-down toplist (dimension=editor_version) and volume graph.
    Family names aren't known at render time, so the columns are built
    client-side (static/js/editors.js)."""
    template_name = 'changesets/editors.html'


class APILandingPageView(TemplateView):
    """Poller status page: the poller's live catch-up batch progress
    (/api/batch-progress/, polled client-side)."""
    template_name = 'changesets/changesets.html'


class ApiDocsView(SpectacularSwaggerView):
    """API / MCP page: how to use the REST API and the MCP server (/mcp), the
    MCP tools (built from the OpenAPI schema, no database access), and the
    generated REST reference (Swagger UI) inside the site's own layout."""
    template_name = 'changesets/api_docs.html'
    title = 'API / MCP'

    # drf-spectacular builds the asset URLs in these two hooks (SWAGGER_UI_DIST would also work,
    # but gives unhashed URLs, so browsers couldn't cache them for long).
    @staticmethod
    def _swagger_ui_resource(filename):
        return static(f'{SWAGGER_UI_VERSION_DIR}/{filename}')

    @staticmethod
    def _swagger_ui_favicon():
        return static(f'{SWAGGER_UI_VERSION_DIR}/favicon-32x32.png')

    @extend_schema(exclude=True)
    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        response.data['mcp_tools'] = public_tools()
        return response
