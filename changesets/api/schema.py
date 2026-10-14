"""OpenAPI (drf-spectacular) parameter definitions shared by several endpoints."""
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes

DIMENSION_FILTER_PARAMS = [
    OpenApiParameter('contributor', OpenApiTypes.STR, description='Restrict to one OSM username (case-insensitive).'),
    OpenApiParameter('editor', OpenApiTypes.STR, description='Restrict to one editor family (case-insensitive).'),
    OpenApiParameter('imagery', OpenApiTypes.STR, description='Restrict to one imagery family (case-insensitive).'),
    OpenApiParameter('language', OpenApiTypes.STR, description='Restrict to one language/locale, as recorded in the changeset (case-insensitive).'),
    OpenApiParameter('country', OpenApiTypes.STR, description='Restrict to one country (ISO 3166-1 alpha-2, e.g. "FR"; case-insensitive), derived from the changeset\'s centroid.'),
]

DATE_PARAMS = [
    OpenApiParameter('start_date', OpenApiTypes.DATE, description='Range start (YYYY-MM-DD). Defaults to 7 days before end_date.'),
    OpenApiParameter('end_date', OpenApiTypes.DATE, description='Range end (YYYY-MM-DD), inclusive. Defaults to today.'),
    OpenApiParameter('hour', OpenApiTypes.INT, description=(
        'Narrow a one-day range (start_date = end_date) to one hour, 0-23 UTC: e.g. the hour of an '
        'hourly timeseries point ("2026-10-09 11:00" -> start_date=end_date=2026-10-09, hour=11).')),
]

FILTER_PARAMS = DATE_PARAMS + DIMENSION_FILTER_PARAMS

# Endpoints counting objects (metric=objects, largest by=objects): count only
# the matching objects, from the replication diffs.
OBJECT_FILTER_PARAMS = [
    OpenApiParameter('action', OpenApiTypes.STR, enum=['create', 'modify', 'delete'], description=(
        'Count only the objects created, modified or deleted (from the replication diffs; '
        'objects count from `objects_since` only).')),
    OpenApiParameter('object_type', OpenApiTypes.STR, enum=['node', 'way', 'relation'], description=(
        'Count only nodes, ways or relations (from the replication diffs).')),
    OpenApiParameter('feature', OpenApiTypes.STR, description=(
        "Count only objects of this feature: the object's main tag key as in dimension=feature "
        "(building, highway, addr, ...; untagged; unknown for deletes).")),
]
