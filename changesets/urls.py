from django.urls import path
from .api.views import (
    ChangesetQueryView, AutocompleteView, BatchProgressView, TimeseriesView, SummaryView, ToplistView, GeoView,
    DistributionView, SizeBreakdownView, LargestView,
)


urlpatterns = [
    path('changesets/', ChangesetQueryView.as_view(), name='changeset-query'),
    path('autocomplete/', AutocompleteView.as_view(), name='autocomplete'),
    path('batch-progress/', BatchProgressView.as_view(), name='batch-progress'),
    path('changesets/timeseries/', TimeseriesView.as_view(), name='changeset-timeseries'),
    path('changesets/summary/', SummaryView.as_view(), name='changeset-summary'),
    path('changesets/toplist/', ToplistView.as_view(), name='changeset-toplist'),
    path('changesets/geo/', GeoView.as_view(), name='changeset-geo'),
    path('changesets/distribution/', DistributionView.as_view(), name='changeset-distribution'),
    path('changesets/distribution/breakdown/', SizeBreakdownView.as_view(), name='changeset-size-breakdown'),
    path('changesets/largest/', LargestView.as_view(), name='changeset-largest'),
]
