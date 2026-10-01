"""Picks the analytics backend serving the API.

Selected by settings.ANALYTICS_BACKEND (env ANALYTICS_BACKEND, default
'timescale'). Backends are imported lazily, so an unused backend's client
library never has to be installed or configured.
"""
from importlib import import_module

from django.conf import settings

_BACKENDS = {
    'timescale': 'changesets.analytics.timescale.backend.TimescaleBackend',
}
_instances = {}


def available_backends():
    return tuple(_BACKENDS)


def get_backend(name=None):
    name = name or settings.ANALYTICS_BACKEND
    if name not in _BACKENDS:
        raise ValueError(f'unknown analytics backend {name!r} (known: {", ".join(_BACKENDS)})')
    if name not in _instances:
        module_path, class_name = _BACKENDS[name].rsplit('.', 1)
        _instances[name] = getattr(import_module(module_path), class_name)()
    return _instances[name]
