from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from changesets.views import APILandingPageView, DashboardView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('changesets.urls')),
    path('changeset_import/', APILandingPageView.as_view(), name='changeset-import'),
    path('', DashboardView.as_view(), name='dashboard'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

