"""ClickHouse analytics backend: SQL over the raw `changesets` table, plus one
daily rollup where measurements asked for it.

Most questions scan the raw table for the date range, reading only the
columns they need. The ones where TimescaleDB's continuous aggregates won by
10-60x (unfiltered or single-filter summary / daily timeseries / toplist on
editor, imagery, language, country, and editor versions) read `daily_rollup`
up to its last covered day and the raw table after it, in one UNION ALL
(schema/0002_rollups.sql: refreshed daily from deduplicated data, ~4M rows).
Autocomplete reads `filter_values` plus the last two days of raw data.

Matches the TimescaleDB backend's observable behavior (see the parity
checker), including how NULL names are grouped, which in Timescale depends on
whether a continuous aggregate or the raw table answered:

- "aggregate-shaped" questions (no filter; or one filter crossed with another
  dimension, daily for timeseries) group untagged imagery/language/country
  under NONE_BUCKET and leave NULL editors/contributors out;
- everything else leaves every NULL name out.

Known, deliberate differences: ties in rankings are broken by name (Postgres
leaves them unordered); with dimension=editor_version every filter applies
(Timescale's aggregate path ignores a language filter there); autocomplete
reads this table's own values (Timescale reads FilterValue, which also lists
countries with no changesets yet).

FINAL makes the ReplacingMergeTree return one version per changeset (an
updated changeset can exist twice until a background merge); measured as
almost free once the table is optimized. The ClickHouseRawBackend variant
skips the rollups (plain SQL only), to measure what they buy.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from ...geo import geohash_bbox_prefix, geohash_decode_center, GEOHASH_PREFIX_UPPER_BOUND_CHAR
from ..base import NONE_BUCKET, EDITOR_VERSION, format_bucket
from .client import get_client

COLUMNS = {
    'contributor': 'user',
    'editor': 'created_by_family',
    'imagery': 'imagery_family',
    'language': 'locale_family',
    'country': 'country_code',
    EDITOR_VERSION: 'created_by',
}
# Dimensions whose untagged rows form a NONE_BUCKET group (and filter).
NONE_DIMENSIONS = {'imagery', 'language', 'country'}
# Timescale has no country x language pair aggregate: that pair falls back to raw.
_NO_PAIR = frozenset({'country', 'language'})

# Dimensions in daily_rollup (contributor isn't: high cardinality, and the
# raw table already beats TimescaleDB there).
ROLLUP_DIMENSIONS = ('editor', 'imagery', 'language', 'country')
WATERMARK_TTL_SECONDS = 300

# Same cap as the web's Postgres statement_timeout (DB_STATEMENT_TIMEOUT_MS).
QUERY_SETTINGS = {'max_execution_time': 30}

RAW_FIELDS = (
    'changeset_id', 'created_at', 'closed_at', 'open', 'changes_count', 'user', 'user_id',
    'min_lat', 'max_lat', 'min_lon', 'max_lon', 'comments_count', 'created_by', 'created_by_family',
    'comment', 'locale', 'source', 'imagery_used', 'hashtags', 'streetcomplete_quest_type',
    'review_requested', 'changesets_count', 'remaining_tags',
)


def _day_start(day):
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


class _Query:
    """WHERE clause + bound parameters for a Filters."""

    def __init__(self, f):
        self.conditions = ['created_at >= {start:DateTime}', 'created_at < {end:DateTime}']
        self.params = {
            'start': _day_start(datetime.strptime(f.start_date, '%Y-%m-%d').date()),
            'end': _day_start(f.end_exclusive),
        }
        for dimension, value in f.dimension_values():
            if not value:
                continue
            column = COLUMNS[dimension]
            if value == NONE_BUCKET and dimension in NONE_DIMENSIONS:
                self.conditions.append(f'{column} IS NULL')
            else:
                self.conditions.append(f'lower({column}) = lower({{{dimension}:String}})')
                self.params[dimension] = value

    def add(self, condition, **params):
        self.conditions.append(condition)
        self.params.update(params)
        return self

    @property
    def where(self):
        return ' AND '.join(self.conditions)


def _aggregate_shaped(f, dimension, daily=True):
    """Whether Timescale answers this grouped question from an aggregate
    (affects NULL-name grouping, see the module docstring)."""
    if not f.any():
        return True
    single = f.single()
    if single is None or not daily:
        return False
    return single[0] != dimension and frozenset({single[0], dimension}) != _NO_PAIR


def _name_expression(dimension, with_none_bucket, query):
    """SELECT expression for a dimension's name, adding the NULL filter when
    NULL names are left out."""
    column = COLUMNS[dimension]
    if with_none_bucket and dimension in NONE_DIMENSIONS:
        return f"coalesce({column}, '{NONE_BUCKET}')"
    query.add(f'{column} IS NOT NULL')
    return column


class ClickHouseBackend:
    name = 'clickhouse'
    final = True
    use_rollups = True
    query_settings = QUERY_SETTINGS  # long-running tools (parity checks) can lift the cap
    _watermark_cache = (float('-inf'), None)

    @property
    def _table(self):
        return 'changesets FINAL' if self.final else 'changesets'

    def _rows(self, sql, params):
        return get_client().query(sql, parameters=params, settings=self.query_settings).result_rows

    # -- rollup routing ------------------------------------------------------

    def _watermark(self):
        """First day daily_rollup doesn't cover (None before its first refresh)."""
        checked, value = self._watermark_cache
        if time.monotonic() - checked > WATERMARK_TTL_SECONDS:
            (day,), = self._rows("SELECT max(day) FROM daily_rollup WHERE dimension = 'volume'", {})
            value = day + timedelta(days=1) if day and day.year > 1970 else None
            self._watermark_cache = (time.monotonic(), value)
        return value

    def _hybrid(self, f):
        """(rollup params, raw _Query) splitting f's range at the watermark, or
        None when the rollup can't be used."""
        watermark = self.use_rollups and self._watermark()
        if not watermark:
            return None
        start = datetime.strptime(f.start_date, '%Y-%m-%d').date()
        split = min(max(watermark, start), f.end_exclusive)
        raw = _Query(f)
        raw.params['start'] = _day_start(split)
        return {'r_start': start, 'r_end': split}, raw

    @staticmethod
    def _rollup_match(f):
        """(dimension, name condition, params) for questions the rollup answers
        exactly like the raw table: no filter, or one filter on a rollup
        dimension (an editor named '(none)' stays raw: it matches nothing)."""
        if not f.any():
            return 'volume', '1', {}
        single = f.single()
        if not single or single[0] not in ROLLUP_DIMENSIONS:
            return None
        dimension, value = single
        if value == NONE_BUCKET:
            return (dimension, "name = '(none)'", {}) if dimension in NONE_DIMENSIONS else None
        return dimension, 'lower(name) = lower({r_value:String})', {'r_value': value}

    @staticmethod
    def _grouped_names(dimension):
        """Rollup name condition and raw name expression for an unfiltered
        group_by/toplist dimension, with the aggregate-shaped NULL policy."""
        column = COLUMNS[dimension]
        if dimension in NONE_DIMENSIONS:
            return '1', f"coalesce({column}, '{NONE_BUCKET}')", ''
        return f"name != '{NONE_BUCKET}'", f"coalesce({column}, '')", f'{column} IS NOT NULL'

    # -- summary -------------------------------------------------------------

    def summary(self, f):
        match = self._rollup_match(f)
        hybrid = match and self._hybrid(f)
        if not hybrid:
            return self._raw_summary(f)
        dimension, condition, params = match
        rollup, raw = hybrid
        (changesets, objects), = self._rows(f"""
            SELECT sum(n), sum(o) FROM (
                SELECT sum(changesets) AS n, sum(objects) AS o FROM daily_rollup
                WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                UNION ALL
                SELECT count(), sum(changes_count) FROM {self._table} WHERE {raw.where})""",
            {**raw.params, **rollup, **params, 'r_dim': dimension})
        return {'total_changesets': changesets, 'total_objects': objects}

    # -- timeseries ----------------------------------------------------------

    def timeseries(self, f, group_by, interval):
        hybrid = interval == 'day' and self._hybrid(f)
        if hybrid and group_by is None and self._rollup_match(f):
            dimension, condition, params = self._rollup_match(f)
            rollup, raw = hybrid
            rows = self._rows(f"""
                SELECT b, sum(n) FROM (
                    SELECT day AS b, changesets AS n FROM daily_rollup
                    WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                    UNION ALL
                    SELECT toDate(created_at) AS b, count() AS n FROM {self._table} WHERE {raw.where} GROUP BY b)
                GROUP BY b ORDER BY b""", {**raw.params, **rollup, **params, 'r_dim': dimension})
            return {'interval': interval, 'dates': [format_bucket(b, interval) for b, _ in rows],
                    'series': [{'name': 'changesets', 'counts': [n for _, n in rows]}]}
        if hybrid and group_by in ROLLUP_DIMENSIONS and not f.any():
            rollup, raw = hybrid
            condition, name, not_null = self._grouped_names(group_by)
            if not_null:
                raw.add(not_null)
            parts = f"""(
                SELECT name, day AS b, changesets AS n FROM daily_rollup
                WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                UNION ALL
                SELECT {name} AS name, toDate(created_at) AS b, count() AS n FROM {self._table} WHERE {raw.where} GROUP BY name, b)"""
            rows = self._rows(f"""
                SELECT name, b, sum(n) FROM {parts}
                WHERE name IN (SELECT name FROM {parts} GROUP BY name ORDER BY sum(n) DESC, name LIMIT 20)
                GROUP BY name, b""", {**raw.params, **rollup, 'r_dim': group_by})
            return self._pivot(rows, interval)
        return self._raw_timeseries(f, group_by, interval)

    @staticmethod
    def _pivot(rows, interval):
        totals, per_name, dates = {}, {}, set()
        for n, b, count in rows:
            label = format_bucket(b, interval)
            per_name.setdefault(n, {})[label] = count
            totals[n] = totals.get(n, 0) + count
            dates.add(label)
        names = sorted(totals, key=lambda n: (-totals[n], n))
        dates = sorted(dates)
        return {'interval': interval, 'dates': dates,
                'series': [{'name': n, 'counts': [per_name[n].get(d, 0) for d in dates]} for n in names]}

    # -- toplist -------------------------------------------------------------

    def toplist(self, f, dimension, metric, limit):
        hybrid = self._hybrid(f)
        only_editor = f.editor and f.editor != NONE_BUCKET and f.single() == ('editor', f.editor)
        if hybrid and not f.any() and dimension in ROLLUP_DIMENSIONS:
            rollup, raw = hybrid
            condition, raw_name, not_null = self._grouped_names(dimension)
            if not_null:
                raw.add(not_null)
            rollup_name, params = 'name', {'r_dim': dimension}
        elif hybrid and dimension == EDITOR_VERSION and only_editor:
            rollup, raw = hybrid
            condition = ("lower(splitByChar(char(0), name)[1]) = lower({r_value:String})"
                         f" AND splitByChar(char(0), name)[2] != '{NONE_BUCKET}'")
            raw_name = "coalesce(created_by, '')"
            raw.add('created_by IS NOT NULL')
            rollup_name, params = "splitByChar(char(0), name)[2]", {'r_dim': EDITOR_VERSION, 'r_value': f.editor}
        else:
            return self._raw_toplist(f, dimension, metric, limit)
        rollup_value, raw_value = ('changesets', 'count()') if metric == 'count' else ('objects', 'sum(changes_count)')
        # `label`, not `name`: ClickHouse resolves aliases in WHERE, so an alias
        # called `name` would shadow daily_rollup's own `name` column in the
        # conditions above (it did, for editor_version).
        rows = self._rows(f"""
            SELECT label, sum(v) AS value FROM (
                SELECT {rollup_name} AS label, sum({rollup_value}) AS v FROM daily_rollup
                WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                GROUP BY label
                UNION ALL
                SELECT {raw_name} AS label, {raw_value} AS v FROM {self._table} WHERE {raw.where} GROUP BY label)
            GROUP BY label ORDER BY value DESC, label LIMIT {int(limit)}""", {**raw.params, **rollup, **params})
        return [{'name': n, 'value': v} for n, v in rows]

    # -- autocomplete --------------------------------------------------------

    def autocomplete(self, field, q):
        if not self.use_rollups:
            return self._raw_autocomplete(field, q)
        column = COLUMNS[field]
        rows = self._rows(f"""
            SELECT DISTINCT value FROM (
                SELECT value FROM filter_values
                WHERE field = {{field:String}} AND positionCaseInsensitiveUTF8(value, {{q:String}}) > 0
                UNION ALL
                SELECT assumeNotNull({column}) AS value FROM changesets
                WHERE created_at >= now() - INTERVAL 2 DAY AND {column} IS NOT NULL
                  AND positionCaseInsensitiveUTF8({column}, {{q:String}}) > 0)
            ORDER BY value LIMIT 10""", {'field': field, 'q': q})
        return [r[0] for r in rows]

    # -- summary -------------------------------------------------------------

    def _raw_summary(self, f):
        q = _Query(f)
        (changesets, objects), = self._rows(
            f'SELECT count(), sum(changes_count) FROM {self._table} WHERE {q.where}', q.params)
        return {'total_changesets': changesets, 'total_objects': objects}

    # -- timeseries ----------------------------------------------------------

    def _raw_timeseries(self, f, group_by, interval):
        bucket = 'toStartOfHour(created_at)' if interval == 'hour' else 'toDate(created_at)'
        q = _Query(f)
        if group_by is None:
            rows = self._rows(
                f'SELECT {bucket} AS b, count() FROM {self._table} WHERE {q.where} GROUP BY b ORDER BY b', q.params)
            return {'interval': interval, 'dates': [format_bucket(b, interval) for b, _ in rows],
                    'series': [{'name': 'changesets', 'counts': [n for _, n in rows]}]}

        name = _name_expression(group_by, _aggregate_shaped(f, group_by, interval == 'day'), q)
        rows = self._rows(
            f"""SELECT {name} AS name, {bucket} AS b, count() AS n FROM {self._table}
                WHERE {q.where} AND {name} IN (
                    SELECT {name} FROM {self._table} WHERE {q.where}
                    GROUP BY {name} ORDER BY count() DESC, {name} LIMIT 20)
                GROUP BY name, b""", q.params)
        totals, per_name, dates = {}, {}, set()
        for n, b, count in rows:
            label = format_bucket(b, interval)
            per_name.setdefault(n, {})[label] = count
            totals[n] = totals.get(n, 0) + count
            dates.add(label)
        names = sorted(totals, key=lambda n: (-totals[n], n))
        dates = sorted(dates)
        return {'interval': interval, 'dates': dates,
                'series': [{'name': n, 'counts': [per_name[n].get(d, 0) for d in dates]} for n in names]}

    # -- toplist -------------------------------------------------------------

    def _raw_toplist(self, f, dimension, metric, limit):
        q = _Query(f)
        with_none = dimension != EDITOR_VERSION and _aggregate_shaped(f, dimension)
        name = _name_expression(dimension, with_none, q)
        value = 'count()' if metric == 'count' else 'sum(changes_count)'
        rows = self._rows(
            f"""SELECT {name} AS name, {value} AS value FROM {self._table} WHERE {q.where}
                GROUP BY name ORDER BY value DESC, name LIMIT {int(limit)}""", q.params)
        return [{'name': n, 'value': v} for n, v in rows]

    # -- geo -----------------------------------------------------------------

    def geo_cells(self, f, prefix_len, bounds):
        q = _Query(f).add('geohash IS NOT NULL')
        crop = None
        if bounds:
            min_lat, max_lat, min_lon, max_lon = bounds
            if f.any():
                # Changesets whose own center is in the viewport (Timescale:
                # `centroid && envelope` on the raw table).
                q.add('(min_lat + max_lat) / 2 BETWEEN {min_lat:Float64} AND {max_lat:Float64}'
                      ' AND (min_lon + max_lon) / 2 BETWEEN {min_lon:Float64} AND {max_lon:Float64}',
                      min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon)
            else:
                # Cells whose center is in the viewport (Timescale's aggregate
                # path): a covering geohash prefix narrows the scan, the exact
                # crop happens below on each cell's decoded center.
                prefix = geohash_bbox_prefix(min_lat, min_lon, max_lat, max_lon)
                if prefix:
                    q.add('geohash >= {lo:String} AND geohash < {hi:String}',
                          lo=prefix, hi=prefix + GEOHASH_PREFIX_UPPER_BOUND_CHAR)
                crop = bounds
        rows = self._rows(
            f"""SELECT substring(geohash, 1, {int(prefix_len)}) AS cell, count(), sum(changes_count)
                FROM {self._table} WHERE {q.where} GROUP BY cell ORDER BY cell""", q.params)
        cells = []
        for cell, count, objects in rows:
            lat, lon = geohash_decode_center(cell)
            if crop:
                min_lat, max_lat, min_lon, max_lon = crop
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue
            cells.append({'lat': lat, 'lon': lon, 'count': count, 'objects': objects})
        return cells

    # -- raw records ---------------------------------------------------------

    def changesets(self, q):
        return _ChangesetPage(self, q)

    # -- autocomplete --------------------------------------------------------

    def _raw_autocomplete(self, field, q):
        column = COLUMNS[field]
        rows = self._rows(
            f"""SELECT DISTINCT {column} FROM changesets
                WHERE {column} IS NOT NULL AND positionCaseInsensitiveUTF8({column}, {{q:String}}) > 0
                ORDER BY {column} LIMIT 10""", {'q': q})
        return [r[0] for r in rows]


class ClickHouseRawBackend(ClickHouseBackend):
    """Plain SQL over the raw table only, without the daily rollup."""
    name = 'clickhouse_raw'
    use_rollups = False


class _ChangesetPage:
    """Newest-first raw records, shaped for DRF's paginator (count() +
    slicing) and ChangesetSerializer (attribute access)."""

    def __init__(self, backend, q):
        self.backend = backend
        self.query = _Query.__new__(_Query)
        self.query.conditions = ['created_at >= {start:DateTime}', 'created_at < {end:DateTime}']
        start, end = q.start, q.end_exclusive
        self.query.params = {
            'start': start if isinstance(start, datetime) else _day_start(datetime.strptime(start, '%Y-%m-%d').date()),
            'end': end if isinstance(end, datetime) else _day_start(end),
        }
        if q.user:
            self.query.add('user = {user:String}', user=q.user)
        if q.editor:
            self.query.add('created_by_family = {editor:String}', editor=q.editor)
        if q.hashtag:
            self.query.add('has(hashtags, {hashtag:String})', hashtag=q.hashtag)
        if q.imagery_raw:
            self.query.add('has(imagery_used, {imagery_raw:String})', imagery_raw=q.imagery_raw)
        if q.imagery_family:
            self.query.add('lower(imagery_family) = lower({imagery_family:String})', imagery_family=q.imagery_family)
        if q.bbox:
            min_lon, min_lat, max_lon, max_lat = q.bbox
            self.query.add('min_lat >= {b_min_lat:Float64} AND max_lat <= {b_max_lat:Float64}'
                           ' AND min_lon >= {b_min_lon:Float64} AND max_lon <= {b_max_lon:Float64}',
                           b_min_lat=min_lat, b_max_lat=max_lat, b_min_lon=min_lon, b_max_lon=max_lon)

    def count(self):
        (n,), = self.backend._rows(f'SELECT count() FROM {self.backend._table} WHERE {self.query.where}', self.query.params)
        return n

    def __len__(self):
        return self.count()

    def __getitem__(self, item):
        start = item.start or 0
        stop = item.stop if item.stop is not None else start + 1000
        rows = self.backend._rows(
            f"""SELECT {', '.join(RAW_FIELDS)} FROM {self.backend._table} WHERE {self.query.where}
                ORDER BY created_at DESC LIMIT {int(stop - start)} OFFSET {int(start)}""", self.query.params)
        return [_Record(dict(zip(RAW_FIELDS, row))) for row in rows]


class _Record:
    """Attribute access for ChangesetSerializer, with Postgres's
    representation: UTC-aware datetimes, JSON tags decoded, empty arrays as
    null (Postgres stores no array when the tag is absent)."""

    def __init__(self, values):
        for key in ('created_at', 'closed_at'):  # the client returns naive UTC datetimes
            if values.get(key) is not None and values[key].tzinfo is None:
                values[key] = values[key].replace(tzinfo=timezone.utc)
        if values.get('remaining_tags') is not None:
            values['remaining_tags'] = json.loads(values['remaining_tags'])
        for key in ('imagery_used', 'hashtags'):
            values[key] = values[key] or None
        self.__dict__.update(values)
