"""Storage writers for ingestion, selected by settings.INGEST_BACKENDS (env
INGEST_BACKENDS, comma-separated, default 'timescale'). Imported lazily, so an
unused backend's client library never has to be installed or configured."""
from importlib import import_module

from django.conf import settings

from .base import ChangesetWriter, WriteResult  # noqa: F401

_WRITERS = {
    'timescale': 'changesets.ingest.writers.timescale.TimescaleWriter',
}
_instances = {}


def get_writer(name):
    if name not in _WRITERS:
        raise ValueError(f'unknown ingest backend {name!r} (known: {", ".join(_WRITERS)})')
    if name not in _instances:
        module_path, class_name = _WRITERS[name].rsplit('.', 1)
        _instances[name] = getattr(import_module(module_path), class_name)()
    return _instances[name]


def get_writers():
    """The configured writers, primary (first) first."""
    return [get_writer(name) for name in settings.INGEST_BACKENDS]
