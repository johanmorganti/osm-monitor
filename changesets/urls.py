from django.urls import path
from .views import ChangesetQueryView, AutocompleteView, BatchProgressView, TimeseriesView, SummaryView, ToplistView, GeoView


urlpatterns = [
    path('changesets/', ChangesetQueryView.as_view(), name='changeset-query'),
    path('autocomplete/', AutocompleteView.as_view(), name='autocomplete'),
    path('batch-progress/', BatchProgressView.as_view(), name='batch-progress'),
    path('changesets/timeseries/', TimeseriesView.as_view(), name='changeset-timeseries'),
    path('changesets/summary/', SummaryView.as_view(), name='changeset-summary'),
    path('changesets/toplist/', ToplistView.as_view(), name='changeset-toplist'),
    path('changesets/geo/', GeoView.as_view(), name='changeset-geo'),
]
