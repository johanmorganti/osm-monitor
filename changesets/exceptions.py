from django.db.utils import OperationalError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """DRF's default handler only formats DRF's own exceptions (ValidationError,
    NotFound, ...) as JSON — anything else, including a query killed by the
    web-only statement_timeout (see docker-compose.yml's DB_STATEMENT_TIMEOUT_MS),
    falls through to Django's generic HTML error page. That breaks any caller
    expecting JSON (dashboard.js's fetch().json() included) and leaks a full
    traceback. Catch OperationalError specifically and return a real JSON
    error instead."""
    response = exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, OperationalError):
        return Response(
            {'error': 'Query took too long and was cancelled — try a narrower date range or filter.'},
            status=503,
        )

    return None
