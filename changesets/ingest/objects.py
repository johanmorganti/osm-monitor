"""Writes parsed replication diffs (osmchange.iter_versions) to the ClickHouse
object tables (schema/0004_object_changes.sql, 0006_object_edits.sql):
per-changeset counts, counts by feature, every object version, and the edits
to existing objects (versions after the first).

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

# Object versions are inserted once their buffer reaches an estimated size
# (Python objects, roughly; the insert makes its own column copy on top). A
# count alone isn't a bound: a daily diff is sorted by type, so a buffer can be
# all relations (2026-10-02: ~15K with ~2.7M members) or all ways. Measured on
# the 2026-09-16 daily diff (7.5M versions): 48 MB buffers (~125K nodes)
# peaked over 512 MB; 16 MB ones stay well under.
FLUSH_BYTES = 16 * 1024 * 1024
_VERSION_BYTES = 700      # the tuple, ints, timestamp, empty containers
_TAG_BYTES = 120          # per tag, plus the key and value lengths
_REF_BYTES = 40           # per way node ref (a list slot + an int)
_MEMBER_BYTES = 200       # per relation member (a tuple, an int, two strings)

COUNT_COLUMNS = [f'{t}_{a}' for t in ('node', 'way', 'rel') for a in ('create', 'modify', 'delete')]
_COUNT_KEY = {(t, a): f'{short}_{a}' for t, short in (('node', 'node'), ('way', 'way'), ('relation', 'rel'))
              for a in ('create', 'modify', 'delete')}
EDIT_COLUMNS = ['timestamp', 'type', 'id', 'version', 'changeset_id', 'uid']
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
        self.buffered_bytes = 0

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
            self.buffered_bytes += (_VERSION_BYTES + _REF_BYTES * len(v.node_refs) + _MEMBER_BYTES * len(v.members)
                                    + sum(_TAG_BYTES + len(k) + len(val) for k, val in v.tags.items()))
            if self.buffered_bytes >= FLUSH_BYTES:
                self._flush_versions()

    def _flush_versions(self):
        if self.versions:
            self.client.insert('object_versions', self.versions, column_names=VERSION_COLUMNS)
            # (timestamp, type, id, version, changeset_id, uid) of versions > 1.
            edits = [(v[5], v[0], v[1], v[2], v[4], v[8]) for v in self.versions if v[2] > 1]
            if edits:
                self.client.insert('object_edits', edits, column_names=EDIT_COLUMNS)
            self.total_versions += len(self.versions)
            self.versions = []
            self.buffered_bytes = 0

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
