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

    if isinstance(exc, OperationalError) or _is_clickhouse_timeout(exc):
        return Response(
            {'error': 'Query took too long and was cancelled — try a narrower date range or filter.'},
            status=503,
        )
    if _is_clickhouse_unavailable(exc):
        return Response({'error': 'Analytics database unavailable — try again in a minute.'}, status=503)

    return None


def _is_clickhouse_timeout(exc):
    """A ClickHouse query cut off by max_execution_time (code 159,
    TIMEOUT_EXCEEDED): same meaning as Postgres's statement_timeout."""
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

