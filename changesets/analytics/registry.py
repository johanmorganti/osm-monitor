"""Picks the analytics backend serving the API.

Selected by settings.ANALYTICS_BACKEND (env ANALYTICS_BACKEND, default
'timescale'). Backends are imported lazily, so an unused backend's client
library never has to be installed or configured.
"""
import hmac
from importlib import import_module

from django.conf import settings

_BACKENDS = {
    'timescale': 'changesets.analytics.timescale.backend.TimescaleBackend',
    'clickhouse': 'changesets.analytics.clickhouse.backend.ClickHouseBackend',
    'clickhouse_raw': 'changesets.analytics.clickhouse.backend.ClickHouseRawBackend',
}
_instances = {}


def available_backends():
    return tuple(_BACKENDS)


def backend_for(request):
    """The backend for one API request: the default, unless the request names
    another in X-Analytics-Backend *and* carries the internal override token
    (settings.ANALYTICS_OVERRIDE_TOKEN, unset = overrides disabled). Lets the
    benchmarks and parity checks hit every backend through the same public
    endpoints without exposing a public switch."""
    token = settings.ANALYTICS_OVERRIDE_TOKEN
    name = request.headers.get('X-Analytics-Backend')
    if name and token and hmac.compare_digest(request.headers.get('X-Analytics-Token', ''), token):
        return get_backend(name)
    return get_backend()


def get_backend(name=None):
    name = name or settings.ANALYTICS_BACKEND
    if name not in _BACKENDS:
        raise ValueError(f'unknown analytics backend {name!r} (known: {", ".join(_BACKENDS)})')
    if name not in _instances:
        module_path, class_name = _BACKENDS[name].rsplit('.', 1)
        _instances[name] = getattr(import_module(module_path), class_name)()
    return _instances[name]
