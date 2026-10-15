"""OpenAPI (drf-spectacular) parameter and response definitions shared by several endpoints."""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes

DIMENSION_FILTER_PARAMS = [
    OpenApiParameter('contributor', OpenApiTypes.STR, description='Restrict to one OSM username (case-insensitive).'),
    OpenApiParameter('editor', OpenApiTypes.STR, description='Restrict to one editor family (case-insensitive).'),
    OpenApiParameter('imagery', OpenApiTypes.STR, description='Restrict to one imagery family (case-insensitive).'),
    OpenApiParameter('language', OpenApiTypes.STR, description='Restrict to one language/locale, as recorded in the changeset (case-insensitive).'),
    OpenApiParameter('country', OpenApiTypes.STR, description='Restrict to one country (ISO 3166-1 alpha-2, e.g. "FR"; case-insensitive), derived from the changeset\'s centroid.'),
]

DATE_PARAMS = [
    OpenApiParameter('start_date', OpenApiTypes.STR, description=(
        'Range start: a day (YYYY-MM-DD) or an ISO 8601 timestamp (UTC unless an offset is given), '
        'e.g. 2026-10-09T11:00:00Z. Defaults to 7 days before end_date.')),
    OpenApiParameter('end_date', OpenApiTypes.STR, description=(
        'Range end: a day (YYYY-MM-DD), included, or a timestamp, excluded: '
        'start_date=2026-10-09T11:00:00Z&end_date=2026-10-09T12:00:00Z is one hour, e.g. one point '
        'of an hourly timeseries. Defaults to today. Ranges that are not whole UTC days read raw '
        'data, so they suit short windows.')),
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

# Every endpoint that queries ClickHouse: a query cut off at its time cap
# (changesets/exceptions.py).
TIMEOUT_RESPONSE = OpenApiResponse(OpenApiTypes.OBJECT, description=(
    'The query took longer than 30 s and was cancelled ({"error": "..."}). Retrying the same '
    'request times out again: narrow the date range or add a filter.'))
