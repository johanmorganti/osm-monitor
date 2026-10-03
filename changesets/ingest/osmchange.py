"""Streaming parser for OSM osmChange files (the minutely and daily
replication diffs): one record per object version, with the feature it maps.

A diff carries each uploaded object version in a <create>, <modify> or
<delete> block, with its new tags and geometry, but never the previous
version: deletes have no tags, and a modify doesn't say what changed.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterator, NamedTuple, Optional

ACTIONS = ('create', 'modify', 'delete')
TYPES = ('node', 'way', 'relation')

# An object's feature is the first of these keys it has: specific POI keys
# before the broad physical ones, so a building with amenity=school counts as
# amenity. A fixed list keeps the number of distinct features bounded.
FEATURE_KEYS = (
    'amenity', 'shop', 'tourism', 'leisure', 'office', 'craft', 'healthcare', 'emergency',
    'historic', 'public_transport', 'railway', 'highway', 'aeroway', 'waterway', 'power',
    'man_made', 'barrier', 'building', 'landuse', 'natural', 'boundary', 'place', 'route',
)
# Keys that don't make an object a feature on their own.
NOISE_KEYS = frozenset({'created_by', 'source', 'note', 'fixme', 'FIXME', 'odbl', 'attribution'})
UNKNOWN = 'unknown'      # deletes: the diff has no tags for them
UNTAGGED = 'untagged'    # mostly the nodes of a way's geometry


class ObjectVersion(NamedTuple):
    type: str
    id: int
    version: int
    action: str
    changeset_id: int
    timestamp: datetime
    uid: int
    user: str
    feature: str
    tags: dict
    lat: Optional[int]        # nodes: degrees x 1e7, OSM's own precision
    lon: Optional[int]
    node_refs: list           # ways
    members: list             # relations: (type, ref, role)


def feature_of(action: str, tags: dict) -> str:
    if action == 'delete':
        return UNKNOWN
    for key in FEATURE_KEYS:
        if key in tags:
            return key
    keys = [k for k in tags if k not in NOISE_KEYS]
    if not keys:
        return UNTAGGED
    if all(k.startswith('addr:') for k in keys):
        return 'addr'
    if tags.get('type') == 'multipolygon':
        return 'multipolygon'
    return 'other'


@lru_cache(maxsize=4096)
def _timestamp(value: str) -> datetime:
    # Cached: a diff's elements share few distinct timestamps.
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _coordinate(value: Optional[str]) -> Optional[int]:
    return round(float(value) * 10_000_000) if value is not None else None


def iter_versions(fileobj) -> Iterator[ObjectVersion]:
    """Object versions of an (uncompressed) osmChange stream, in file order.
    Streams: each element is cleared once read, so memory stays flat even on
    a ~100 MB daily diff."""
    action = block = None
    for event, el in ET.iterparse(fileobj, events=('start', 'end')):
        tag = el.tag
        if event == 'start':
            if tag in ACTIONS:
                action, block = tag, el
            continue
        if tag not in TYPES:
            continue
        a = el.attrib
        tags, refs, members = {}, [], []
        for child in el:
            if child.tag == 'tag':
                tags[child.get('k')] = child.get('v')
            elif child.tag == 'nd':
                refs.append(int(child.get('ref')))
            elif child.tag == 'member':
                members.append((child.get('type'), int(child.get('ref')), child.get('role') or ''))
        yield ObjectVersion(
            tag, int(a['id']), int(a['version']), action, int(a['changeset']), _timestamp(a['timestamp']),
            int(a.get('uid', 0)), a.get('user', ''), feature_of(action, tags), tags,
            _coordinate(a.get('lat')), _coordinate(a.get('lon')), refs, members,
        )
        # Drop what's been read: an element cleared on its own would stay
        # attached to its block, and a daily diff's blocks hold millions.
        if block is not None:
            block.clear()
