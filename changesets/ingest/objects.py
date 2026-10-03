"""Writes parsed replication diffs (osmchange.iter_versions) to the ClickHouse
object tables (schema/0004_object_changes.sql): per-changeset counts, counts
by feature, and every object version.

Idempotent like the changeset writers (changesets/ingest/writers/base.py):
every row is keyed by the file it came from (source + sequence), so writing a
file again replaces its rows.
"""
from collections import Counter, defaultdict

from ..analytics.clickhouse.client import get_client
from .osmchange import iter_versions

# object_versions' retention (its TTL in 0004_object_changes.sql), and how far
# back poll_diffs backfills by default.
OBJECT_VERSIONS_DAYS = 92

# Buffered before an insert, whichever comes first: object versions, or way
# node refs + relation members. The second bound matters on a daily diff,
# which is sorted by type: its last ~15K versions are relations with ~2.7M
# members, enough to exceed the 512 MB container on their own.
FLUSH_VERSIONS = 50_000
FLUSH_ITEMS = 500_000

COUNT_COLUMNS = [f'{t}_{a}' for t in ('node', 'way', 'rel') for a in ('create', 'modify', 'delete')]
_COUNT_KEY = {(t, a): f'{short}_{a}' for t, short in (('node', 'node'), ('way', 'way'), ('relation', 'rel'))
              for a in ('create', 'modify', 'delete')}
VERSION_COLUMNS = ['type', 'id', 'version', 'action', 'changeset_id', 'timestamp', 'source', 'sequence',
                   'uid', 'user', 'feature', 'tags', 'lat', 'lon', 'node_refs', 'members']


class DiffWriter:
    """Buffers the versions of one or more diff files, then writes them.
    Counts are inserted only in write(), after every version: if a write
    fails half-way, the file is simply written again."""

    def __init__(self):
        self.client = get_client()
        self.versions = []
        self.counts = defaultdict(Counter)        # (changeset, source, sequence) -> column -> n
        self.edit_time = {}                       # (changeset, source, sequence) -> latest timestamp
        self.features = Counter()                 # (changeset, source, sequence, type, action, feature) -> n
        self.total_versions = 0
        self.items = 0

    def add_file(self, fileobj, source, sequence):
        """Parse one uncompressed osmChange stream into the buffer."""
        for v in iter_versions(fileobj):
            key = (v.changeset_id, source, sequence)
            self.counts[key][_COUNT_KEY[(v.type, v.action)]] += 1
            latest = self.edit_time.get(key)
            if latest is None or v.timestamp > latest:
                self.edit_time[key] = v.timestamp
            self.features[(*key, v.type, v.action, v.feature)] += 1
            self.versions.append((v.type, v.id, v.version, v.action, v.changeset_id, v.timestamp, source, sequence,
                                  v.uid, v.user, v.feature, v.tags, v.lat, v.lon, v.node_refs, v.members))
            self.items += len(v.node_refs) + len(v.members)
            if len(self.versions) >= FLUSH_VERSIONS or self.items >= FLUSH_ITEMS:
                self._flush_versions()

    def _flush_versions(self):
        if self.versions:
            self.client.insert('object_versions', self.versions, column_names=VERSION_COLUMNS)
            self.total_versions += len(self.versions)
            self.versions = []
            self.items = 0

    def write(self):
        """Insert everything buffered; returns (versions, changesets)."""
        self._flush_versions()
        if self.counts:
            self.client.insert(
                'object_changes',
                [(*key, self.edit_time[key], *(c[col] for col in COUNT_COLUMNS)) for key, c in self.counts.items()],
                column_names=['changeset_id', 'source', 'sequence', 'edit_time', *COUNT_COLUMNS])
            self.client.insert(
                'object_change_features',
                [(*key, n) for key, n in self.features.items()],
                column_names=['changeset_id', 'source', 'sequence', 'type', 'action', 'feature', 'count'])
        result = (self.total_versions, len({key[0] for key in self.counts}))
        self.counts.clear()
        self.edit_time.clear()
        self.features.clear()
        self.total_versions = 0
        return result
