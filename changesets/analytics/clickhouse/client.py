"""ClickHouse client shared by the ClickHouse analytics backend and writer.

One HTTP client per thread: gunicorn serves requests from several threads, and
a clickhouse-connect client shouldn't run concurrent queries."""
import threading

import clickhouse_connect
from django.conf import settings

_local = threading.local()


def get_client():
    client = getattr(_local, 'client', None)
    if client is None:
        client = clickhouse_connect.get_client(**settings.CLICKHOUSE, autogenerate_session_id=False)
        _local.client = client
    return client
