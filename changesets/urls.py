from django.urls import path
from .views import ChangesetListView, ChangesetQueryView, ChangesetDetailView, AutocompleteView, BatchProgressView, ImportJobView, ImageryAuditView, DashboardDataView, redirect_to_landing_page


urlpatterns = [
    path('changesets/', ChangesetQueryView.as_view(), name='changeset-query'),
    path('autocomplete/', AutocompleteView.as_view(), name='autocomplete'),
    path('batch-progress/', BatchProgressView.as_view(), name='batch-progress'),
    path('imagery-audit/', ImageryAuditView.as_view(), name='imagery-audit'),
    path('dashboard/', DashboardDataView.as_view(), name='dashboard-data'),
    path('changesets/<int:changeset_id>/', ChangesetDetailView.as_view(), name='changeset-detail'),
    path('sequence/<int:seq_start>/<int:seq_end>/', ChangesetListView.as_view(), name='changeset-list'),
    path('import-job/<int:job_id>/', ImportJobView.as_view(), name='import-job'),
    path('sequence/', redirect_to_landing_page),
    path('', redirect_to_landing_page),
]
