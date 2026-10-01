"""OpenAPI (drf-spectacular) parameter definitions shared by several endpoints."""
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes

FILTER_PARAMS = [
    OpenApiParameter('start_date', OpenApiTypes.DATE, description='Range start (YYYY-MM-DD). Defaults to 7 days before end_date.'),
    OpenApiParameter('end_date', OpenApiTypes.DATE, description='Range end (YYYY-MM-DD), inclusive. Defaults to today.'),
    OpenApiParameter('contributor', OpenApiTypes.STR, description='Restrict to one OSM username (case-insensitive).'),
    OpenApiParameter('editor', OpenApiTypes.STR, description='Restrict to one editor family (case-insensitive).'),
    OpenApiParameter('imagery', OpenApiTypes.STR, description='Restrict to one imagery family (case-insensitive).'),
    OpenApiParameter('language', OpenApiTypes.STR, description='Restrict to one language/locale, as recorded in the changeset (case-insensitive).'),
    OpenApiParameter('country', OpenApiTypes.STR, description='Restrict to one country (ISO 3166-1 alpha-2, e.g. "FR"; case-insensitive), derived from the changeset\'s centroid.'),
]
