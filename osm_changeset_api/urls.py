from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView
from changesets.mcp.server import mcp_endpoint
from changesets.views import APILandingPageView, ApiDocsView, DashboardView, EditorsView, ObjectsView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('changesets.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('api/docs/', ApiDocsView.as_view(url_name='api-schema'), name='api-docs'),
    # With or without the trailing slash: a redirect would lose an MCP client's POST.
    re_path(r'^mcp/?$', mcp_endpoint, name='mcp'),
    path('changeset_import/', APILandingPageView.as_view(), name='changeset-import'),
    path('objects/', ObjectsView.as_view(), name='objects'),
    path('editors/', EditorsView.as_view(), name='editors'),
    path('', DashboardView.as_view(), name='dashboard'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

