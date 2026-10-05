"""The contract between the public API (changesets/api/) and an analytics
backend (changesets/analytics/<name>/).

The API layer owns everything that defines the public behavior: parameter
parsing and validation, defaults, interval choice, geohash precision per
viewport, response shapes. A backend only answers the questions below, in
whatever way suits its database (ClickHouse: rollups up to a watermark, raw
scans after it; TimescaleDB's continuous aggregates until 2026-10-05). Results are plain Python data shaped like the
JSON the API returns, so every backend can be compared byte for byte through
the same endpoints.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional, Protocol, Sequence

# Changesets with no imagery/language/country tag are grouped and filtered
# under this literal name, never silently excluded (see CLAUDE.md's
# "NULL-tag volume gets its own '(none)' bucket" section). Part of the public
# API contract: filtering by it must match the NULL rows.
NONE_BUCKET = '(none)'

# The five public dimension names (API params, group_by/dimension values).
# See CLAUDE.md's dimension-naming table for how they map to DB columns.
DIMENSIONS = ('contributor', 'editor', 'imagery', 'language', 'country')

# Toplist-only dimension: exact editor version within one editor family.
EDITOR_VERSION = 'editor_version'

# Toplist / timeseries group_by only (no filter): campaign hashtags, from the
# changeset's `hashtags` tag, matched case-insensitively and reported lower-case.
HASHTAG = 'hashtag'

# Timeseries group_by / toplist dimensions over the objects themselves (the
# replication diffs, see docs/decisions/object-changes.md), metric 'objects'
# only, no filter: what was done (create/modify/delete), to what
# (node/way/relation), and the feature (osmchange.feature_of).
OBJECT_DIMENSIONS = ('action', 'object_type', 'feature')

def format_bucket(bucket, interval):
    """Render a bucket as the API's date label. `bucket` is a datetime for
    hourly buckets but a plain date for daily ones, hence the hasattr check.

    The hour is always zero-padded: callers sort on these formatted strings
    (per-name/per-bucket pivot), where an unpadded "...9:00" would sort after
    "...10:00" and scramble the x-axis."""
    day = bucket.date() if hasattr(bucket, 'date') else bucket
    return f'{day.isoformat()} {bucket.hour:02d}:00' if interval == 'hour' else day.isoformat()


@dataclass(frozen=True)
class Filters:
    """Date range (whole days, inclusive, YYYY-MM-DD) plus the optional
    case-insensitive dimension filters shared by summary/timeseries/toplist/geo."""
    start_date: str
    end_date: str
    contributor: str = ''
    editor: str = ''
    imagery: str = ''
    language: str = ''
    country: str = ''

    @property
    def end_exclusive(self) -> date:
        return datetime.strptime(self.end_date, '%Y-%m-%d').date() + timedelta(days=1)

    def dimension_values(self):
        """(dimension, value) for every dimension, in DIMENSIONS order."""
        return [(name, getattr(self, name)) for name in DIMENSIONS]

    def any(self) -> bool:
        return any(value for _, value in self.dimension_values())

    def single(self):
        """(dimension, value) if exactly one dimension filter is set, else None."""
        set_filters = [(n, v) for n, v in self.dimension_values() if v]
        return set_filters[0] if len(set_filters) == 1 else None

    def as_dict(self) -> dict:
        """The `filters` object echoed back in API responses (key order matters
        for byte-identical responses)."""
        return {'start_date': self.start_date, 'end_date': self.end_date, **dict(self.dimension_values())}


@dataclass(frozen=True)
class ChangesetQuery:
    """Raw-record listing parameters (/api/changesets/). Unlike Filters, these
    are exact-match and the date bounds are already resolved: `start` and
    `end_exclusive` are either 'YYYY-MM-DD' strings or datetimes."""
    start: object
    end_exclusive: object
    user: str = ''
    editor: str = ''
    hashtag: str = ''
    imagery_raw: str = ''
    imagery_family: str = ''
    bbox: Optional[tuple] = None  # (min_lon, min_lat, max_lon, max_lat), changeset bbox fully inside


class AnalyticsBackend(Protocol):
    name: str

    def summary(self, f: Filters) -> dict:
        """{'total_changesets': int|None, 'total_objects': int|None} over the range."""

    def timeseries(self, f: Filters, group_by: Optional[str], interval: str, metric: str = 'count') -> dict:
        """{'interval', 'dates', 'series'}: plain volume when group_by is None,
        else the top 20 names of that dimension (or HASHTAG), one series each.
        `interval` ('hour'|'day') is chosen by the API layer; `metric` is
        'count' (changesets) or 'objects' (objects changed), and also ranks
        the top 20. With group_by in OBJECT_DIMENSIONS (metric 'objects'),
        counts come from the diffs and start at object_coverage()."""

    def toplist(self, f: Filters, dimension: str, metric: str, limit: int) -> list:
        """[{'name', 'value'}] ranked by metric ('count'|'objects'), at most
        `limit`. OBJECT_DIMENSIONS: as for timeseries."""

    def most_edited(self, f: Filters, days: int, limit: int) -> list:
        """The `limit` objects with the most edits (versions after the first)
        in the last `days` days up to now, f's date range ignored and its
        dimension filters applied to each edit's changeset: dicts type, id,
        edits, contributors, changesets, last_edit, version (the latest),
        name (its latest name tag in the window, '' if none)."""

    def object_coverage(self) -> Optional[date]:
        """First day the object dimensions cover (every upload of a changeset
        created that day or later is in the diffs), or None without data."""

    def size_counts(self, f: Filters) -> list:
        """[(changes_count, changesets)]: how many changesets have each exact
        size. Exact and small (OSM caps a changeset at 10,000 changes today, a
        few tens of thousands historically), so the API derives histogram
        buckets and percentiles from it."""

    def size_quantiles(self, f: Filters, by: str, quantiles: Sequence[float], limit: int) -> list:
        """[{'name', 'changesets', 'objects', 'quantiles': [...]}], exact
        changes_count quantiles per group. `by` is a dimension (top `limit`
        names by changesets, NULL names left out) or 'day' (every day, named
        'YYYY-MM-DD', in order)."""

    def largest(self, f: Filters, by: str, limit: int) -> list:
        """The `limit` largest changesets by 'objects' (changes_count) or
        'area' (bounding box, km²), as dicts: changeset_id, created_at, user,
        editor, changes_count, area_km2 (None without a bbox), country,
        comment."""

    def geo_cells(self, f: Filters, prefix_len: int, bounds: Optional[tuple]) -> list:
        """[{'cell', 'lat', 'lon', 'count', 'objects'}] per geohash cell of
        `prefix_len` characters (`cell` = that geohash prefix, lat/lon its
        center); `bounds` = (min_lat, max_lat, min_lon, max_lon) or None."""

    def cell_changesets(self, f: Filters, cell: str, limit: int, offset: int) -> list:
        """Changesets of one geo_cells cell (geohash starting with `cell`),
        newest first, in the `largest` record shape."""

    def changesets(self, q: ChangesetQuery) -> Sequence:
        """Newest-first records matching q, as a sliceable sequence with
        count() (what DRF's paginator needs)."""

    def autocomplete(self, field: str, q: str) -> list:
        """Up to 10 known values of `field` containing q (case-insensitive)."""
