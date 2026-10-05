from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """DRF's default handler only formats DRF's own exceptions (ValidationError,
    NotFound, ...) as JSON — anything else, including a ClickHouse query cut
    off by its time cap, falls through to Django's generic HTML error page.
    That breaks any caller expecting JSON (dashboard.js's fetch().json()
    included) and leaks a full traceback, so those get a real JSON error."""
    response = exception_handler(exc, context)
    if response is not None:
        return response

    if _is_clickhouse_timeout(exc):
        return Response(
            {'error': 'Query took too long and was cancelled — try a narrower date range or filter.'},
            status=503,
        )
    if _is_clickhouse_unavailable(exc):
        return Response({'error': 'Analytics database unavailable — try again in a minute.'}, status=503)
    if isinstance(exc, NotImplementedError):
        # A question the configured analytics backend doesn't answer.
        return Response({'error': f'Not supported by this analytics backend: {exc}'}, status=501)

    return None


def _is_clickhouse_timeout(exc):
    """A ClickHouse query cut off by max_execution_time (code 159,
    TIMEOUT_EXCEEDED)."""
    try:
        from clickhouse_connect.driver.exceptions import DatabaseError
    except ImportError:
        return False
    return isinstance(exc, DatabaseError) and 'TIMEOUT_EXCEEDED' in str(exc)


def _is_clickhouse_unavailable(exc):
    """ClickHouse unreachable or still starting (connection refused, a table
    still loading): a temporary 503, not a 500 with an HTML page."""
    try:
        from clickhouse_connect.driver.exceptions import OperationalError as ClickHouseOperationalError
    except ImportError:
        return False
    return isinstance(exc, ClickHouseOperationalError)

