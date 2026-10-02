"""Compare two analytics backends on a generated set of questions.

Drives the backends directly (not over HTTP) with a seeded mix of every
operation, dimension, filter (real top values plus NONE_BUCKET), filter pair,
metric, map resolution and date range, and compares results by meaning rather
than bytes: map cells as a set, ranking ties at the cut-off allowed to differ,
raw records by id. Reports exact matches, near matches with their magnitude,
and timings.

    python manage.py check_backend_parity --a timescale --b clickhouse --cases 200
"""
import json
import random
import time
from collections import Counter
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import connection

from changesets.analytics import DIMENSIONS, EDITOR_VERSION, NONE_BUCKET, ChangesetQuery, Filters, get_backend
from changesets.api.params import pick_interval
from changesets.geo import GEOHASH_PREFIX_LENGTH, geohash_precision_for_bbox

LAST_DAY = date(2026, 9, 30)  # stable: before both databases received live data differently
RANGES = {
    'week': 7, 'month': 31, 'quarter': 92, 'year': 365,
}
BBOXES = {  # (min_lon, min_lat, max_lon, max_lat)
    'paris': (2.20, 48.80, 2.45, 48.92), 'france': (-5.2, 41.3, 9.6, 51.1), 'tokyo': (139.5, 35.5, 139.95, 35.85),
    'nyc': (-74.1, 40.6, -73.8, 40.9), 'lagos': (3.2, 6.4, 3.6, 6.7), 'europe': (-10.0, 35.0, 30.0, 60.0),
}
NONE_DIMENSIONS = {'imagery', 'language', 'country'}


def date_range(rng, kind):
    if kind == 'all':
        return Filters('2005-04-01', LAST_DAY.isoformat())
    days = RANGES[kind]
    end = LAST_DAY - timedelta(days=rng.randrange(0, 365 * 3))
    start = end - timedelta(days=days - 1)
    return Filters(start.isoformat(), end.isoformat())


def with_filters(f, **filters):
    return Filters(f.start_date, f.end_date, **filters)


# -- normalization -----------------------------------------------------------

def compare_ranking(a, b, key='name', value='value'):
    """Rankings equal except for which names fill the last tied value."""
    if len(a) != len(b):
        return False, f'length {len(a)} vs {len(b)}'
    if not a:
        return True, ''
    cut = a[-1][value]
    if b[-1][value] != cut:
        return False, f'cut-off value {cut} vs {b[-1][value]}'
    strict_a = {r[key]: r[value] for r in a if r[value] != cut}
    strict_b = {r[key]: r[value] for r in b if r[value] != cut}
    if strict_a != strict_b:
        diff = {k: (strict_a.get(k), strict_b.get(k)) for k in set(strict_a) | set(strict_b) if strict_a.get(k) != strict_b.get(k)}
        return False, f'{len(diff)} names differ, e.g. {list(diff.items())[:3]}'
    return True, ''


def compare(op, a, b):
    if op == 'summary':
        # As the API presents them: an empty sum is null on one side, 0 on the other.
        a = {k: v or 0 for k, v in a.items()}
        b = {k: v or 0 for k, v in b.items()}
        same = a == b
        return same, '' if same else f'{a} vs {b}'
    if op == 'timeseries':
        if a['interval'] != b['interval']:
            return False, 'interval'
        def series(r):
            return {s['name']: {d: c for d, c in zip(r['dates'], s['counts']) if c} for s in r['series']}
        sa, sb = series(a), series(b)
        if sa == sb:
            return True, ''
        if len(sa) == len(sb) == 20:  # top-20 names: allow ties on the last total
            ta = sorted(({'name': n, 'value': sum(v.values())} for n, v in sa.items()), key=lambda r: -r['value'])
            tb = sorted(({'name': n, 'value': sum(v.values())} for n, v in sb.items()), key=lambda r: -r['value'])
            ok, why = compare_ranking(ta, tb)
            shared = set(sa) & set(sb)
            if ok and all(sa[n] == sb[n] for n in shared):
                return True, 'tie at top-20 cut-off'
        names = set(sa) | set(sb)
        bad = [n for n in names if sa.get(n) != sb.get(n)]
        worst = max((abs(sum(sa.get(n, {}).values()) - sum(sb.get(n, {}).values())) for n in bad), default=0)
        return False, f'{len(bad)}/{len(names)} series differ (largest total gap {worst}), e.g. {bad[:3]}'
    if op == 'toplist':
        return compare_ranking(a, b)
    if op == 'geo':
        ca = {(c['lat'], c['lon']): (c['count'], c['objects']) for c in a}
        cb = {(c['lat'], c['lon']): (c['count'], c['objects']) for c in b}
        if ca == cb:
            return True, ''
        keys = set(ca) | set(cb)
        bad = [k for k in keys if ca.get(k) != cb.get(k)]
        gap = sum(abs((ca.get(k, (0, 0))[0]) - (cb.get(k, (0, 0))[0])) for k in bad)
        return False, f'{len(bad)}/{len(keys)} cells differ, changeset count gap {gap}'
    if op == 'changesets':
        (na, ra), (nb, rb) = a, b
        if na != nb:
            return False, f'count {na} vs {nb}'
        if not ra:
            return True, ''
        boundary = min(r['created_at'] for r in ra + rb)
        ia = {r['changeset_id']: r for r in ra if r['created_at'] > boundary}
        ib = {r['changeset_id']: r for r in rb if r['created_at'] > boundary}
        if ia.keys() != ib.keys():
            return False, f'ids differ ({len(ia.keys() ^ ib.keys())})'
        bad = [i for i in ia if ia[i] != ib[i]]
        if bad:
            fields = Counter(k for i in bad for k in ia[i] if ia[i][k] != ib[i].get(k))
            return False, f'{len(bad)} records differ in {dict(fields)}'
        return True, ''
    if op == 'autocomplete':
        return a == b, '' if a == b else f'{a} vs {b}'
    raise ValueError(op)


def involves_country(f, kw):
    """Country filter, group_by or dimension: differences there can come from
    the data rather than the query, since rows located by the old PostGIS
    trigger and by the Python locator disagree on ~0.0004% (CLAUDE.md's
    "Location data is computed at ingest")."""
    return bool(getattr(f, 'country', '')) or 'country' in (kw.get('group_by'), kw.get('dimension'))


def raw_records(backend, q):
    page = backend.changesets(q)
    count = page.count()
    rows = page[0:100]
    fields = ('changeset_id', 'created_at', 'closed_at', 'open', 'changes_count', 'user', 'user_id', 'min_lat',
              'max_lat', 'min_lon', 'max_lon', 'comments_count', 'created_by', 'created_by_family', 'comment',
              'locale', 'source', 'imagery_used', 'hashtags', 'streetcomplete_quest_type', 'review_requested',
              'changesets_count', 'remaining_tags')
    return count, [{k: getattr(r, k, None) for k in fields} for r in rows]


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('--a', default='timescale')
        parser.add_argument('--b', default='clickhouse')
        parser.add_argument('--cases', type=int, default=200)
        parser.add_argument('--seed', type=int, default=1)
        parser.add_argument('--json', help='write every case and result here')

    def handle(self, *args, **options):
        connection.cursor().execute("SET statement_timeout = 0")
        a, b = get_backend(options['a']), get_backend(options['b'])
        for backend in (a, b):  # no web-request time cap here (Postgres's is lifted above)
            if hasattr(backend, 'query_settings'):
                backend.query_settings = {}
        rng = random.Random(options['seed'])

        # Real filter values: each dimension's top values over a recent year.
        year = Filters('2025-01-01', '2025-12-31')
        values = {d: [r['name'] for r in a.toplist(year, d, 'count', 8) if r['name'] != NONE_BUCKET] for d in DIMENSIONS}
        for d in NONE_DIMENSIONS:
            values[d].append(NONE_BUCKET)

        def random_filters(f, max_filters):
            dims = rng.sample(DIMENSIONS, rng.randint(0, max_filters))
            return with_filters(f, **{d: rng.choice(values[d]) for d in dims}), dims

        cases = []
        for i in range(options['cases']):
            op = rng.choices(['summary', 'timeseries', 'toplist', 'geo', 'changesets', 'autocomplete'],
                             weights=[3, 4, 5, 3, 2, 1])[0]
            # Full history only without filters: filtered full-history scans
            # time out on the Timescale side.
            kind = rng.choice(['week', 'month', 'quarter', 'year', 'all'])
            f = date_range(rng, kind)
            if op == 'summary':
                f, dims = random_filters(f, 0 if kind == 'all' else 2)
                cases.append((op, f, {}))
            elif op == 'timeseries':
                f, dims = random_filters(f, 0 if kind == 'all' else 2)
                group_by = rng.choice([None, *DIMENSIONS])
                cases.append((op, f, {'group_by': group_by, 'interval': pick_interval(f.start_date, f.end_date, None)}))
            elif op == 'toplist':
                f, dims = random_filters(f, 0 if kind == 'all' else 2)
                dimension = rng.choice([*DIMENSIONS, EDITOR_VERSION])
                if dimension == EDITOR_VERSION:
                    f = with_filters(f, **{**{d: getattr(f, d) for d in DIMENSIONS}, 'editor': rng.choice(values['editor']), 'language': ''})
                cases.append((op, f, {'dimension': dimension, 'metric': rng.choice(['count', 'objects']), 'limit': rng.choice([10, 20, 50])}))
            elif op == 'geo':
                f, dims = random_filters(f, 0 if kind in ('all', 'year') else 1)
                if rng.random() < 0.5:
                    name = rng.choice(list(BBOXES))
                    min_lon, min_lat, max_lon, max_lat = BBOXES[name]
                    cases.append((op, f, {'prefix_len': geohash_precision_for_bbox(min_lat, min_lon, max_lat, max_lon),
                                          'bounds': (min_lat, max_lat, min_lon, max_lon), 'where': name}))
                else:
                    cases.append((op, f, {'prefix_len': GEOHASH_PREFIX_LENGTH['coarse'], 'bounds': None}))
            elif op == 'changesets':
                f = date_range(rng, 'week')
                kw = rng.choice([{}, {'editor': rng.choice(values['editor'])}, {'user': rng.choice(values['contributor'])},
                                 {'imagery_family': rng.choice([v for v in values['imagery'] if v != NONE_BUCKET])},
                                 {'bbox': BBOXES[rng.choice(['paris', 'nyc', 'tokyo'])]}])
                q = ChangesetQuery(start=f.start_date, end_exclusive=f.end_exclusive, **kw)
                cases.append((op, q, {}))
            else:
                cases.append((op, None, {'field': rng.choice(DIMENSIONS), 'q': rng.choice(['jo', 'ma', 'de', 'st', 'fr', 'e'])}))

        def run(backend, op, f, kw):
            if op == 'summary':
                return backend.summary(f)
            if op == 'timeseries':
                return backend.timeseries(f, kw['group_by'], kw['interval'])
            if op == 'toplist':
                return backend.toplist(f, kw['dimension'], kw['metric'], kw['limit'])
            if op == 'geo':
                return backend.geo_cells(f, kw['prefix_len'], kw['bounds'])
            if op == 'changesets':
                return raw_records(backend, f)
            return backend.autocomplete(kw['field'], kw['q'])

        results, outcome, timings = [], Counter(), {'a': [], 'b': []}
        for n, (op, f, kw) in enumerate(cases, 1):
            record = {'op': op, 'filters': getattr(f, '__dict__', None) and {k: v for k, v in vars(f).items() if v},
                      **{k: v for k, v in kw.items()}}
            try:
                t = time.perf_counter(); ra = run(a, op, f, kw); ta = time.perf_counter() - t
                t = time.perf_counter(); rb = run(b, op, f, kw); tb = time.perf_counter() - t
                same, why = compare(op, ra, rb)
                status = ('autocomplete-' if op == 'autocomplete' else '') + ('same' if same else 'DIFF')
                if not same and involves_country(f, kw):
                    status = 'DIFF-country'
                record.update(status=status, detail=why, a_ms=round(ta * 1000), b_ms=round(tb * 1000))
                timings['a'].append(ta); timings['b'].append(tb)
            except Exception as e:  # keep going: a timeout or error is itself a finding
                record.update(status='ERROR', detail=f'{type(e).__name__}: {str(e)[:200]}')
            outcome[record['status']] += 1
            results.append(record)
            if record['status'] not in ('same', 'autocomplete-same'):
                self.stdout.write(f"[{n}/{len(cases)}] {record['status']} {op} {json.dumps({k: v for k, v in record.items() if k not in ('status', 'op')}, default=str)[:400]}")

        self.stdout.write(f'\n{len(cases)} cases: ' + ', '.join(f'{k} {v}' for k, v in sorted(outcome.items())))
        self.stdout.write(f"total time: {options['a']} {sum(timings['a']):.0f}s, {options['b']} {sum(timings['b']):.0f}s")
        if options['json']:
            with open(options['json'], 'w') as out:
                json.dump({'a': options['a'], 'b': options['b'], 'seed': options['seed'], 'outcome': outcome, 'cases': results},
                          out, indent=1, default=str)
