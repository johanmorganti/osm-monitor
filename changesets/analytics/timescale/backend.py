"""TimescaleDB analytics backend: continuous aggregates where one covers the
question, the raw `changesets_changeset` hypertable otherwise.

Routing, per operation:
- unfiltered: the dimension's (or total volume's) CAgg
- exactly one filter: that dimension's CAgg filtered by name (summary, and
  timeseries without group_by)
- one filter crossed with a different dimension (toplist dimension or
  timeseries group_by): that pair's daily CAgg (caggs.PAIR_CAGGS)
- anything else (2+ filters, filter == dimension, hourly cross-dimension):
  the raw hypertable, filtered by exact canonical values so compressed
  chunks' bloom filters apply (canonical.py)
"""
from collections import defaultdict

from django.db.models import Count, Sum
from django.db.models.expressions import RawSQL
from django.db.models.functions import TruncDate, TruncHour, Substr

from ...geo import (
    viewport_overlap_sql, geohash_decode_center, geohash_bbox_prefix,
    geohash_prefix_range_sql, GEOHASH_PREFIX_UPPER_BOUND_CHAR,
)
from ...models import Changeset, FilterValue, CaggVolumeHourly, CaggEditorVersionDaily, CaggGeoHashedDaily
from ..base import NONE_BUCKET, EDITOR_VERSION, format_bucket
from .caggs import (
    DIMENSION_FIELDS, RAW_ONLY_DIMENSIONS, CAGG_MODELS, CAGG_MODELS_HOURLY, CAGG_VOLUME_MODELS,
    pair_cagg_lookup,
)
from .canonical import canonical_values


def filtered_changesets(f):
    """Raw hypertable rows for a Filters. Half-open range on the raw datetime
    (not created_at__date) so chunk exclusion and the created_at index apply;
    dimension filters use exact canonical values (see canonical.py), with
    NONE_BUCKET meaning the column is NULL."""
    changesets = Changeset.objects.filter(created_at__gte=f.start_date, created_at__lt=f.end_exclusive)
    if f.contributor:
        changesets = changesets.filter(user__in=canonical_values('contributor', f.contributor))
    if f.editor:
        changesets = changesets.filter(created_by_family__in=canonical_values('editor', f.editor))
    if f.imagery:
        changesets = changesets.filter(imagery_family__isnull=True) if f.imagery == NONE_BUCKET else changesets.filter(imagery_family__in=canonical_values('imagery', f.imagery))
    if f.language:
        changesets = changesets.filter(locale_family__isnull=True) if f.language == NONE_BUCKET else changesets.filter(locale_family__in=canonical_values('language', f.language))
    if f.country:
        changesets = changesets.filter(country_code__isnull=True) if f.country == NONE_BUCKET else changesets.filter(country_code__in=canonical_values('country', f.country))
    return changesets


def _pivot_top_names(rows, names, name_key, count_key, interval):
    """Shared shape of every grouped timeseries: one series per name (in
    ranking order), one point per bucket, 0 where a name has no row."""
    per_name_bucket = defaultdict(dict)
    dates = set()
    for r in rows:
        label = format_bucket(r['bucket'], interval)
        per_name_bucket[r[name_key]][label] = r[count_key]
        dates.add(label)
    dates = sorted(dates)
    series = [{'name': name, 'counts': [per_name_bucket[name].get(d, 0) for d in dates]} for name in names]
    return {'interval': interval, 'dates': dates, 'series': series}


class TimescaleBackend:
    name = 'timescale'

    # -- summary -------------------------------------------------------------

    def summary(self, f):
        single = f.single()
        if not f.any():
            return CaggVolumeHourly.objects.filter(bucket__gte=f.start_date, bucket__lt=f.end_exclusive).aggregate(
                total_changesets=Sum('cnt'), total_objects=Sum('changes_sum'))
        if single:
            dimension, value = single
            return CAGG_MODELS[dimension].objects.filter(
                name__iexact=value, bucket__gte=f.start_date, bucket__lt=f.end_exclusive
            ).aggregate(total_changesets=Sum('cnt'), total_objects=Sum('changes_sum'))
        return filtered_changesets(f).aggregate(total_changesets=Count('id'), total_objects=Sum('changes_count'))

    # -- timeseries ----------------------------------------------------------

    def timeseries(self, f, group_by, interval):
        single = f.single()
        pair = pair_cagg_lookup(single[0], group_by) if single and group_by is not None and interval == 'day' else None

        if not f.any():
            return self._timeseries_from_rollups(f, group_by, interval)
        if single and group_by is None:
            # Plain volume for exactly one filtered dimension: that dimension's
            # own CAgg answers it directly, same fast path as summary().
            dimension, value = single
            return self._timeseries_single_filtered(f, dimension, value, interval)
        if pair:
            return self._timeseries_from_pair(f, single[1], pair)
        return self._timeseries_from_raw(f, group_by, interval)

    def _timeseries_single_filtered(self, f, dimension, value, interval):
        # Summed per bucket: the case-insensitive filter can match several
        # stored spellings ("Bing" and "bing"), one CAgg row each per bucket.
        # Taking rows as they came duplicated those buckets' date labels.
        model = (CAGG_MODELS_HOURLY if interval == 'hour' else CAGG_MODELS)[dimension]
        rows = list(
            model.objects.filter(name__iexact=value, bucket__gte=f.start_date, bucket__lt=f.end_exclusive)
            .values('bucket').annotate(cnt=Sum('cnt')).order_by('bucket')
        )
        dates = [format_bucket(r['bucket'], interval) for r in rows]
        return {'interval': interval, 'dates': dates, 'series': [{'name': 'changesets', 'counts': [r['cnt'] for r in rows]}]}

    def _timeseries_from_pair(self, f, filter_value, pair):
        """`pair` is (model, filter_column, group_by_column). Daily only (every
        pair CAgg is), so interval is always 'day' here."""
        model, filter_col, group_col = pair
        filter_kwargs = {f'{filter_col}__iexact': filter_value}
        top = list(
            model.objects.filter(bucket__gte=f.start_date, bucket__lt=f.end_exclusive, **filter_kwargs)
            .values(group_col).annotate(total_count=Sum('cnt')).order_by('-total_count')[:20]
        )
        names = [row[group_col] for row in top]
        if not names:
            return {'interval': 'day', 'dates': [], 'series': []}
        # Summed per (bucket, name): several spellings of the filter value
        # ("GeoPortal" / "Geoportal") give several rows per bucket and name,
        # which the pivot would otherwise overwrite instead of adding up.
        rows = (
            model.objects.filter(
                bucket__gte=f.start_date, bucket__lt=f.end_exclusive,
                **{f'{group_col}__in': names}, **filter_kwargs,
            )
            .values('bucket', group_col).annotate(cnt=Sum('cnt'))
        )
        return _pivot_top_names(rows, names, group_col, 'cnt', 'day')

    def _timeseries_from_rollups(self, f, group_by, interval):
        if group_by is None:
            volume = list(
                CAGG_VOLUME_MODELS[interval].objects.filter(bucket__gte=f.start_date, bucket__lt=f.end_exclusive)
                .order_by('bucket')
            )
            dates = [format_bucket(v.bucket, interval) for v in volume]
            return {'interval': interval, 'dates': dates, 'series': [{'name': 'changesets', 'counts': [v.cnt for v in volume]}]}

        model = (CAGG_MODELS_HOURLY if interval == 'hour' else CAGG_MODELS).get(group_by)
        if model is None:
            return {'interval': interval, 'dates': [], 'series': []}
        top = list(
            model.objects.filter(bucket__gte=f.start_date, bucket__lt=f.end_exclusive)
            .values('name').annotate(total_count=Sum('cnt')).order_by('-total_count')[:20]
        )
        names = [row['name'] for row in top]
        if not names:
            return {'interval': interval, 'dates': [], 'series': []}
        rows = (
            model.objects.filter(name__in=names, bucket__gte=f.start_date, bucket__lt=f.end_exclusive)
            .values('bucket', 'name', 'cnt')
        )
        return _pivot_top_names(rows, names, 'name', 'cnt', interval)

    def _timeseries_from_raw(self, f, group_by, interval):
        changesets = filtered_changesets(f)
        trunc = TruncHour('created_at') if interval == 'hour' else TruncDate('created_at')

        if group_by is None:
            rows = list(
                changesets.annotate(bucket=trunc)
                .values('bucket').annotate(count=Count('id')).order_by('bucket')
            )
            dates = [format_bucket(r['bucket'], interval) for r in rows]
            return {'interval': interval, 'dates': dates, 'series': [{'name': 'changesets', 'counts': [r['count'] for r in rows]}]}

        field = DIMENSION_FIELDS[group_by]
        top = list(
            changesets.filter(**{f'{field}__isnull': False})
            .values(field).annotate(count=Count('id')).order_by('-count')[:20]
        )
        names = [row[field] for row in top]
        if not names:
            return {'interval': interval, 'dates': [], 'series': []}
        rows = (
            changesets.filter(**{f'{field}__in': names})
            .annotate(bucket=trunc)
            .values('bucket', field).annotate(count=Count('id'))
        )
        return _pivot_top_names(rows, names, field, 'count', interval)

    # -- toplist -------------------------------------------------------------

    def toplist(self, f, dimension, metric, limit):
        if dimension == EDITOR_VERSION:
            # Its own CAgg only when editor is the sole filter that matters
            # here (contributor/imagery/country send it to raw; language is
            # ignored on the CAgg path, as before), raw otherwise.
            if f.contributor or f.imagery or f.country:
                return self._toplist_from_raw(f, dimension, metric, limit)
            return self._toplist_editor_version(f, metric, limit)

        single = f.single()
        pair = pair_cagg_lookup(single[0], dimension) if single else None
        if not f.any():
            return self._toplist_from_rollups(f, dimension, metric, limit)
        if pair:
            return self._toplist_from_pair(f, single[1], pair, metric, limit)
        return self._toplist_from_raw(f, dimension, metric, limit)

    def _toplist_from_rollups(self, f, dimension, metric, limit):
        agg_field = 'cnt' if metric == 'count' else 'changes_sum'
        rows = (
            CAGG_MODELS[dimension].objects.filter(bucket__gte=f.start_date, bucket__lt=f.end_exclusive)
            .values('name').annotate(value=Sum(agg_field)).order_by('-value')[:limit]
        )
        return [{'name': r['name'], 'value': r['value']} for r in rows]

    def _toplist_from_pair(self, f, filter_value, pair, metric, limit):
        """`pair` is (model, filter_column, dimension_column). iexact on the
        filter side matches the raw path's case-insensitivity."""
        model, filter_col, dimension_col = pair
        agg_field = 'cnt' if metric == 'count' else 'changes_sum'
        rows = (
            model.objects.filter(
                bucket__gte=f.start_date, bucket__lt=f.end_exclusive,
                **{f'{filter_col}__iexact': filter_value},
            )
            .values(dimension_col).annotate(value=Sum(agg_field)).order_by('-value')[:limit]
        )
        return [{'name': r[dimension_col], 'value': r['value']} for r in rows]

    def _toplist_editor_version(self, f, metric, limit):
        agg_field = 'cnt' if metric == 'count' else 'changes_sum'
        rows = (
            CaggEditorVersionDaily.objects.filter(
                bucket__gte=f.start_date, bucket__lt=f.end_exclusive, editor__iexact=f.editor,
            )
            .values('version').annotate(value=Sum(agg_field)).order_by('-value')[:limit]
        )
        return [{'name': r['version'], 'value': r['value']} for r in rows]

    def _toplist_from_raw(self, f, dimension, metric, limit):
        field = DIMENSION_FIELDS.get(dimension) or RAW_ONLY_DIMENSIONS[dimension]
        agg = Count('id') if metric == 'count' else Sum('changes_count')
        rows = (
            filtered_changesets(f).filter(**{f'{field}__isnull': False})
            .values(field).annotate(value=agg).order_by('-value')[:limit]
        )
        return [{'name': r[field], 'value': r['value']} for r in rows]

    # -- geo -----------------------------------------------------------------

    def geo_cells(self, f, prefix_len, bounds):
        if not f.any():
            return self._geo_from_rollups(f, prefix_len, bounds)
        return self._geo_from_raw(f, prefix_len, bounds)

    def _geo_from_rollups(self, f, prefix_len, bounds):
        rows = CaggGeoHashedDaily.objects.filter(bucket__gte=f.start_date, bucket__lt=f.end_exclusive)
        if bounds:
            # The CAgg stores only the geohash key, no geometry to overlap
            # against, but the longest common prefix of the bbox's SW/NE
            # corners keeps this a sargable range scan instead of pulling every
            # geohash on Earth for the range (measured 2026-09-19: ~870K
            # distinct geohashes for a 1.5-month range without it).
            min_lat, max_lat, min_lon, max_lon = bounds
            prefix = geohash_bbox_prefix(min_lat, min_lon, max_lat, max_lon)
            if prefix:
                rows = rows.annotate(
                    in_prefix=RawSQL(geohash_prefix_range_sql(), [prefix, prefix + GEOHASH_PREFIX_UPPER_BOUND_CHAR]),
                ).filter(in_prefix=True)
        rows = (
            rows.annotate(cell=Substr('geohash', 1, prefix_len))
            .values('cell').annotate(count=Sum('cnt'), objects=Sum('changes_sum'))
        )
        cells = []
        for r in rows:
            lat, lon = geohash_decode_center(r['cell'])
            # The covering prefix only guarantees containing the bbox, not
            # being tight to it, so the exact crop happens here.
            if bounds:
                min_lat, max_lat, min_lon, max_lon = bounds
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue
            cells.append({'lat': lat, 'lon': lon, 'count': r['count'], 'objects': r['objects']})
        return cells

    def _geo_from_raw(self, f, prefix_len, bounds):
        # Viewport scoping uses the GiST-indexed `centroid` column (migration
        # 0029) via `&&`, cheaper than decoding geohash per row.
        changesets = filtered_changesets(f).filter(geohash__isnull=False).annotate(cell=Substr('geohash', 1, prefix_len))
        if bounds:
            min_lat, max_lat, min_lon, max_lon = bounds
            changesets = changesets.annotate(
                in_viewport=RawSQL(viewport_overlap_sql(), [min_lon, min_lat, max_lon, max_lat]),
            ).filter(in_viewport=True)
        rows = changesets.values('cell').annotate(count=Count('id'), objects=Sum('changes_count'))
        cells = []
        for r in rows:
            lat, lon = geohash_decode_center(r['cell'])
            cells.append({'lat': lat, 'lon': lon, 'count': r['count'], 'objects': r['objects']})
        return cells

    # -- raw records ---------------------------------------------------------

    def changesets(self, q):
        qs = Changeset.objects.order_by('-created_at').filter(created_at__gte=q.start).filter(created_at__lt=q.end_exclusive)
        if q.user:
            qs = qs.filter(user=q.user)
        if q.editor:
            qs = qs.filter(created_by_family=q.editor)
        if q.hashtag:
            qs = qs.filter(hashtags__contains=[q.hashtag])
        if q.imagery_raw:
            qs = qs.filter(imagery_used__contains=[q.imagery_raw])
        if q.imagery_family:
            qs = qs.filter(imagery_family__iexact=q.imagery_family)
        if q.bbox:
            min_lon, min_lat, max_lon, max_lat = q.bbox
            qs = qs.filter(min_lat__gte=min_lat, max_lat__lte=max_lat, min_lon__gte=min_lon, max_lon__lte=max_lon)
        return qs

    # -- autocomplete --------------------------------------------------------

    def autocomplete(self, field, q):
        # FilterValue holds one row per distinct value ever seen (not per
        # changeset), so this stays fast at any history size.
        return list(
            FilterValue.objects.filter(field=field, value__icontains=q)
            .order_by('value').values_list('value', flat=True)[:10]
        )
