"""ClickHouse analytics backend: SQL over the raw `changesets` table, plus one
daily rollup where measurements asked for it.

Most questions scan the raw table for the date range, reading only the
columns they need. The ones where TimescaleDB's continuous aggregates won by
10-60x (unfiltered or single-filter summary / daily timeseries / toplist on
editor, imagery, language, country, and editor versions) read `daily_rollup`
up to its last covered day and the raw table after it, in one UNION ALL
(schema/0002_rollups.sql: refreshed daily from deduplicated data, ~4M rows).
The unfiltered map does the same with `geo_coarse_daily` / `geo_cells_daily`
(schema/0003_geo_rollups.sql).
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
from datetime import date, datetime, timedelta, timezone

from ...geo import geohash_bbox_cover, geohash_decode_center, GEOHASH_PREFIX_UPPER_BOUND_CHAR
from ..base import NONE_BUCKET, EDITOR_VERSION, HASHTAG, OBJECT_DIMENSIONS, format_bucket
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
# Rollup tables (schema/0002, 0003) and where to read each one's last covered day.
ROLLUP_WATERMARKS = {
    'daily_rollup': "daily_rollup WHERE dimension = 'volume'",
    'geo_cells_daily': 'geo_cells_daily',
    'geo_coarse_daily': 'geo_coarse_daily',
    'object_daily_rollup': "object_daily_rollup WHERE dimension = 'volume'",
}
# Object dimensions (OBJECT_DIMENSIONS): their column in object_change_features
# and object_daily_rollup (schema/0004, 0005).
OBJECT_COLUMNS = {'action': 'toString(action)', 'object_type': 'toString(type)', 'feature': 'feature'}
# Precision geo_coarse_daily is stored at (GEOHASH_PREFIX_LENGTH['coarse']).
GEO_COARSE_PRECISION = 3

# Value expressions per metric: raw table, then rollup tables.
RAW_VALUE = {'count': 'count()', 'objects': 'sum(changes_count)'}
ROLLUP_VALUE = {'count': 'changesets', 'objects': 'objects'}

# One row per (changeset, distinct lower-cased hashtag): a changeset counts
# once under each of its campaigns.
HASHTAG_JOIN = 'ARRAY JOIN arrayDistinct(arrayMap(x -> lower(x), hashtags)) AS hashtag_name'

# Bounding-box area in km² on a sphere of the Earth's mean radius: exact for a
# lat/lon rectangle, which is what a changeset bbox is.
AREA_KM2 = ('40589732.5 * radians(max_lon - min_lon)'
            ' * (sin(radians(max_lat)) - sin(radians(min_lat)))')

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
        # Parenthesized, so a condition containing OR stays one condition.
        return ' AND '.join(f'({c})' for c in self.conditions)


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
    _watermark_cache = {}  # rollup table -> (checked at, first uncovered day)

    @property
    def _table(self):
        return 'changesets FINAL' if self.final else 'changesets'

    def _rows(self, sql, params):
        return get_client().query(sql, parameters=params, settings=self.query_settings).result_rows

    # -- rollup routing ------------------------------------------------------

    def _watermark(self, table):
        """First day a rollup table doesn't cover (None before its first refresh)."""
        checked, value = self._watermark_cache.get(table, (float('-inf'), None))
        if time.monotonic() - checked > WATERMARK_TTL_SECONDS:
            (day,), = self._rows(f'SELECT max(day) FROM {ROLLUP_WATERMARKS[table]}', {})
            value = day + timedelta(days=1) if day and day.year > 1970 else None
            self._watermark_cache[table] = (time.monotonic(), value)
        return value

    def _hybrid(self, f, table='daily_rollup'):
        """(rollup params, raw _Query) splitting f's range at the rollup
        table's watermark, or None when the rollup can't be used."""
        watermark = self.use_rollups and self._watermark(table)
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

    def timeseries(self, f, group_by, interval, metric='count'):
        if group_by in OBJECT_DIMENSIONS:
            data = self._pivot(self._object_rows(f, group_by, interval), interval)
            data['series'] = data['series'][:20]
            return data
        rollup_value, raw_value = ROLLUP_VALUE[metric], RAW_VALUE[metric]
        hybrid = interval == 'day' and self._hybrid(f)
        if hybrid and group_by is None and self._rollup_match(f):
            dimension, condition, params = self._rollup_match(f)
            rollup, raw = hybrid
            rows = self._rows(f"""
                SELECT b, sum(n) FROM (
                    SELECT day AS b, {rollup_value} AS n FROM daily_rollup
                    WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                    UNION ALL
                    SELECT toDate(created_at) AS b, {raw_value} AS n FROM {self._table} WHERE {raw.where} GROUP BY b)
                GROUP BY b ORDER BY b""", {**raw.params, **rollup, **params, 'r_dim': dimension})
            return {'interval': interval, 'dates': [format_bucket(b, interval) for b, _ in rows],
                    'series': [{'name': 'changesets', 'counts': [n for _, n in rows]}]}
        if hybrid and group_by in ROLLUP_DIMENSIONS and not f.any():
            rollup, raw = hybrid
            condition, name, not_null = self._grouped_names(group_by)
            if not_null:
                raw.add(not_null)
            parts = f"""(
                SELECT name, day AS b, {rollup_value} AS n FROM daily_rollup
                WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                UNION ALL
                SELECT {name} AS name, toDate(created_at) AS b, {raw_value} AS n FROM {self._table} WHERE {raw.where} GROUP BY name, b)"""
            rows = self._rows(f"""
                SELECT name, b, sum(n) FROM {parts}
                WHERE name IN (SELECT name FROM {parts} GROUP BY name ORDER BY sum(n) DESC, name LIMIT 20)
                GROUP BY name, b""", {**raw.params, **rollup, 'r_dim': group_by})
            return self._pivot(rows, interval)
        return self._raw_timeseries(f, group_by, interval, metric)

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
        if dimension in OBJECT_DIMENSIONS:
            rows = sorted(self._object_rows(f, dimension, None), key=lambda r: (-r[2], r[0]))
            return [{'name': n, 'value': v} for n, _, v in rows[:limit]]
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

    # -- object dimensions ---------------------------------------------------
    # Counts from the replication diffs (schema/0004), attributed to the
    # changeset's created_at day: object_daily_rollup (0005) up to its
    # watermark when it answers the filters (as daily_rollup does), then the
    # raw counts joined to `changesets` for the date and the filters.

    def object_coverage(self):
        checked, value = self._watermark_cache.get('object_coverage', (float('-inf'), None))
        if time.monotonic() - checked > WATERMARK_TTL_SECONDS:
            # The day after the earliest edit: a changeset created then or later
            # has every upload in the diffs (same rule as the rollup).
            (day,), = self._rows('SELECT toDate(min(edit_time)) + 1 FROM object_changes', {})
            value = day if day and day.year > 1971 else None
            self._watermark_cache['object_coverage'] = (time.monotonic(), value)
        return value

    def most_edited(self, f, days, limit):
        params = {'days': int(days)}
        in_changesets = ''
        if f.any():
            # Edits come up to 24 h after their changeset was opened.
            q = _Query(f)
            q.params['start'] = _day_start(datetime.now(timezone.utc).date() - timedelta(days=days + 1))
            q.params['end'] = _day_start(datetime.now(timezone.utc).date() + timedelta(days=1))
            in_changesets = f'AND changeset_id IN (SELECT changeset_id FROM {self._table} WHERE {q.where})'
            params.update(q.params)
        window = f'timestamp >= now() - INTERVAL {{days:UInt32}} DAY {in_changesets}'
        # Ranked on the count alone over object_edits (ordered by time, so
        # the week is all it reads), then the details of those objects from
        # object_versions (ordered by object, so a lookup), filtered the same
        # way. Distinct contributors and changesets for every object of the
        # week would cost ~3x more. `(type, id) IN` on the columns
        # themselves, never toString(type): that keeps the sort-key lookup
        # (2.5 s -> 0.2 s).
        top = self._rows(f"""
            SELECT type, id FROM object_edits FINAL WHERE {window}
            GROUP BY type, id ORDER BY count() DESC, type, id LIMIT {int(limit)}""", params)
        if not top:
            return []
        params['objects'] = [(str(t), i) for t, i in top]
        details = {(str(t), i): row for t, i, *row in self._rows(f"""
            SELECT type, id, uniqExactIf(version, version > 1), uniqExactIf(uid, version > 1),
                   uniqExactIf(changeset_id, version > 1), maxIf(timestamp, version > 1), max(version),
                   argMax(tags['name'], version)
            FROM object_versions WHERE {window} AND (type, id) IN {{objects:Array(Tuple(String, UInt64))}}
            GROUP BY type, id""", params)}
        results = []
        for key in params['objects']:
            edits, contributors, changesets, last_edit, version, name = details[key]
            results.append({'type': key[0], 'id': key[1], 'edits': edits, 'contributors': contributors,
                            'changesets': changesets, 'last_edit': last_edit.replace(tzinfo=timezone.utc),
                            'version': version, 'name': name})
        return results

    def _object_rollup_start(self):
        checked, value = self._watermark_cache.get('object_rollup_start', (float('-inf'), None))
        if time.monotonic() - checked > WATERMARK_TTL_SECONDS:
            (value,), = self._rows("SELECT min(day) FROM object_daily_rollup WHERE dimension = 'volume'", {})
            self._watermark_cache['object_rollup_start'] = (time.monotonic(), value)
        return value

    def _object_rows(self, f, dimension, interval):
        """(name, bucket, objects) rows of an object dimension, bucketed by
        interval ('hour' | 'day') or not at all (None, bucket 0), clamped to
        object_coverage()."""
        coverage = self.object_coverage()
        start = max(datetime.strptime(f.start_date, '%Y-%m-%d').date(), coverage or date.max)
        end = f.end_exclusive
        if start >= end:
            return []
        column = OBJECT_COLUMNS[dimension]
        parts, params = [], {}
        match = interval != 'hour' and self._rollup_match(f)
        watermark = match and self.use_rollups and self._watermark('object_daily_rollup')
        # The rollup starts where coverage started when it was last refreshed;
        # while the backfill still extends coverage backward, earlier days are
        # only in the raw tables.
        if watermark and start < self._object_rollup_start():
            watermark = None
        split = min(max(watermark, start), end) if watermark else start
        if split > start:
            r_dim, condition, match_params = match
            parts.append(f"""
                SELECT {column} AS label, {'day' if interval else '0'} AS b, sum(objects) AS n FROM object_daily_rollup
                WHERE dimension = {{r_dim:String}} AND {condition} AND day >= {{r_start:Date}} AND day < {{r_end:Date}}
                GROUP BY label, b""")
            params.update(match_params, r_dim=r_dim, r_start=start, r_end=split)
        if split < end:
            raw = _Query(f)
            raw.params['start'] = _day_start(split)
            bucket = {'day': 'toDate(c.created_at)', 'hour': 'toStartOfHour(c.created_at)', None: '0'}[interval]
            # The IN narrows the read to the matching changesets (the table is
            # ordered by changeset_id); the join brings their created_at.
            parts.append(f"""
                SELECT {column} AS label, {bucket} AS b, sum(count) AS n
                FROM object_change_features FINAL
                INNER JOIN (SELECT changeset_id, created_at FROM {self._table} WHERE {raw.where}) AS c USING changeset_id
                WHERE changeset_id IN (SELECT changeset_id FROM {self._table} WHERE {raw.where})
                GROUP BY label, b""")
            params.update(raw.params)
        return self._rows(
            f"SELECT label, b, sum(n) FROM ({' UNION ALL '.join(parts)}) GROUP BY label, b", params)

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

    def _raw_timeseries(self, f, group_by, interval, metric='count'):
        bucket = 'toStartOfHour(created_at)' if interval == 'hour' else 'toDate(created_at)'
        value = RAW_VALUE[metric]
        q = _Query(f)
        if group_by is None:
            rows = self._rows(
                f'SELECT {bucket} AS b, {value} FROM {self._table} WHERE {q.where} GROUP BY b ORDER BY b', q.params)
            return {'interval': interval, 'dates': [format_bucket(b, interval) for b, _ in rows],
                    'series': [{'name': 'changesets', 'counts': [n for _, n in rows]}]}

        source, name = self._grouped_source(f, group_by, interval == 'day', q)
        rows = self._rows(
            f"""SELECT {name} AS name, {bucket} AS b, {value} AS n FROM {source}
                WHERE {q.where} AND {name} IN (
                    SELECT {name} FROM {source} WHERE {q.where}
                    GROUP BY {name} ORDER BY {value} DESC, {name} LIMIT 20)
                GROUP BY name, b""", q.params)
        return self._pivot(rows, interval)

    def _grouped_source(self, f, dimension, daily, q):
        """(FROM clause, name expression) to group raw rows by `dimension`,
        adding the NULL filter to q when NULL names are left out."""
        if dimension == HASHTAG:
            return f'{self._table} {HASHTAG_JOIN}', 'hashtag_name'
        with_none = dimension != EDITOR_VERSION and _aggregate_shaped(f, dimension, daily)
        return self._table, _name_expression(dimension, with_none, q)

    # -- toplist -------------------------------------------------------------

    def _raw_toplist(self, f, dimension, metric, limit):
        q = _Query(f)
        source, name = self._grouped_source(f, dimension, True, q)
        value = RAW_VALUE[metric]
        rows = self._rows(
            f"""SELECT {name} AS name, {value} AS value FROM {source} WHERE {q.where}
                GROUP BY name ORDER BY value DESC, name LIMIT {int(limit)}""", q.params)
        return [{'name': n, 'value': v} for n, v in rows]

    # -- geo -----------------------------------------------------------------

    def geo_cells(self, f, prefix_len, bounds):
        q = _Query(f).add('geohash IS NOT NULL')
        crop = None
        cover = []
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
                # path): the geohash cells covering it narrow the scan, the
                # exact crop happens below on each cell's decoded center.
                cover = geohash_bbox_cover(min_lat, min_lon, max_lat, max_lon)
                crop = bounds
        rows = self._geo_rows(f, q, int(prefix_len), cover)
        cells = []
        for cell, count, objects in rows:
            lat, lon = geohash_decode_center(cell)
            if crop:
                min_lat, max_lat, min_lon, max_lon = crop
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue
            cells.append({'cell': cell, 'lat': lat, 'lon': lon, 'count': count, 'objects': objects})
        return cells

    def _geo_rows(self, f, q, prefix_len, cover):
        """(cell, changesets, objects) rows, from a geo rollup up to its
        watermark when unfiltered (geo_coarse_daily when its precision is
        enough, else geo_cells_daily) and the raw table after it."""
        cover_params = {}
        for i, prefix in enumerate(cover):
            cover_params[f'g{i}lo'] = prefix
            cover_params[f'g{i}hi'] = prefix + GEOHASH_PREFIX_UPPER_BOUND_CHAR

        def in_cover(column):
            return ' OR '.join(
                f'({column} >= {{g{i}lo:String}} AND {column} < {{g{i}hi:String}})'
                for i in range(len(cover))) or '1'

        if prefix_len <= GEO_COARSE_PRECISION:
            table, use_rollup = 'geo_coarse_daily', True
        else:
            # geo_cells_daily is ordered by cell, so it reads the covered
            # cells' whole history: a country-wide viewport (covering cells
            # of 1-2 characters) over a short range is faster on the raw
            # table. Measured: France, 3 months: rollup 1.56 s, raw 0.36 s;
            # 2 years: 1.63 s vs 2.03 s; Paris or Tokyo: the rollup wins at
            # every range (0.12-0.22 s against 0.5-30 s).
            days = (f.end_exclusive - datetime.strptime(f.start_date, '%Y-%m-%d').date()).days
            table, use_rollup = 'geo_cells_daily', (cover and len(cover[0]) > 2) or days > 2 * 365
        hybrid = use_rollup and not f.any() and self._hybrid(f, table)
        if not hybrid:
            q.add(in_cover('geohash'), **cover_params)
            return self._rows(
                f"""SELECT substring(geohash, 1, {prefix_len}) AS cell, count(), sum(changes_count)
                    FROM {self._table} WHERE {q.where} GROUP BY cell ORDER BY cell""", q.params)
        rollup, raw = hybrid
        raw.add('geohash IS NOT NULL').add(in_cover('geohash'))
        return self._rows(
            f"""SELECT cell, sum(n), sum(o) FROM (
                    SELECT substring(cell, 1, {prefix_len}) AS cell, sum(changesets) AS n, sum(objects) AS o
                    FROM {table} WHERE day >= {{r_start:Date}} AND day < {{r_end:Date}} AND ({in_cover('cell')})
                    GROUP BY cell
                    UNION ALL
                    SELECT substring(geohash, 1, {prefix_len}), count(), sum(changes_count)
                    FROM {self._table} WHERE {raw.where} GROUP BY 1)
                GROUP BY cell ORDER BY cell""",
            {**raw.params, **rollup, **cover_params})

    # -- object analysis -----------------------------------------------------
    # Raw table only: changes_count has a small exact domain (OSM caps a
    # changeset at 10,000 changes; ~34K distinct values over full history), so
    # grouping by it first keeps exact quantiles cheap at any range.

    def size_counts(self, f):
        q = _Query(f)
        return self._rows(
            f"""SELECT changes_count, count() FROM {self._table} WHERE {q.where}
                GROUP BY changes_count ORDER BY changes_count""", q.params)

    def size_quantiles(self, f, by, quantiles, limit):
        q = _Query(f)
        if by == 'day':
            key, order = 'toDate(created_at)', 'name'
        else:
            column = COLUMNS[by]
            key, order = column, f'changesets DESC, name LIMIT {int(limit)}'
            q.add(f'{column} IS NOT NULL')
        levels = ', '.join(repr(float(level)) for level in quantiles)
        rows = self._rows(
            f"""SELECT name, sum(n) AS changesets, sum(c * n) AS objects,
                       quantilesExactWeighted({levels})(c, n)
                FROM (SELECT {key} AS name, changes_count AS c, count() AS n
                      FROM {self._table} WHERE {q.where} GROUP BY name, c)
                GROUP BY name ORDER BY {order}""", q.params)
        return [{'name': name.isoformat() if by == 'day' else name, 'changesets': changesets, 'objects': objects,
                 'quantiles': list(values)} for name, changesets, objects, values in rows]

    def largest(self, f, by, limit):
        q = _Query(f)
        if by == 'area':
            q.add('min_lat IS NOT NULL AND max_lat IS NOT NULL AND min_lon IS NOT NULL AND max_lon IS NOT NULL')
            order = 'area_km2 DESC'
        else:
            order = 'changes_count DESC'
        return self._records(q, f'{order}, changeset_id DESC', limit)

    def cell_changesets(self, f, cell, limit, offset):
        # Same membership as geo_cells: the changeset's own geohash starts
        # with the cell's (shorter) geohash.
        q = _Query(f).add('startsWith(geohash, {cell:String})', cell=cell)
        return self._records(q, 'created_at DESC, changeset_id DESC', limit, offset)

    def _records(self, q, order, limit, offset=0):
        """Changeset summaries (largest / cell_changesets shape) for q."""
        fields = ('changeset_id', 'created_at', 'user', 'editor', 'changes_count', 'area_km2', 'country', 'comment')
        rows = self._rows(
            f"""SELECT changeset_id, created_at, user, created_by_family, changes_count,
                       {AREA_KM2} AS area_km2, country_code, comment
                FROM {self._table} WHERE {q.where}
                ORDER BY {order} LIMIT {int(limit)} OFFSET {int(offset)}""", q.params)
        records = []
        for row in rows:
            record = dict(zip(fields, row))
            record['created_at'] = record['created_at'].replace(tzinfo=timezone.utc)
            if record['area_km2'] is not None:
                record['area_km2'] = round(record['area_km2'], 1)
            records.append(record)
        return records

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
