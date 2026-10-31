from datetime import date, datetime
from django.test import SimpleTestCase
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from changesets.api.params import BadParameter, parse_bbox, parse_bound, pick_interval, resolve_filters, fill_buckets, param_with_alias
from changesets.analytics import Filters
from changesets.api.views import ChangesetPagination


def request(**query):
    return Request(APIRequestFactory().get('/', query))


class ResolveFiltersTests(SimpleTestCase):
    def test_days_pass_through_unchanged(self):
        f = resolve_filters(request(start_date='2026-10-01', end_date='2026-10-20'))
        self.assertEqual((f.start_date, f.end_date), ('2026-10-01', '2026-10-20'))
        self.assertIsNone(f.start_time)

    def test_lenient_day_still_accepted(self):
        # The backends parse days with strptime, which accepts this: keep it working.
        f = resolve_filters(request(start_date='2026-1-5', end_date='2026-10-20'))
        self.assertEqual(f.start_date, '2026-1-5')

    def test_missing_bound_defaults_to_last_week(self):
        f = resolve_filters(request())
        self.assertEqual((f.end_date > f.start_date), True)
        # A lone bound is kept and the other defaulted (LoneBoundTests).

    def test_timestamps_give_an_exact_window(self):
        f = resolve_filters(request(start_date='2026-10-09T11:00:00Z', end_date='2026-10-09T12:00:00Z'))
        self.assertEqual((f.start_date, f.end_date), ('2026-10-09', '2026-10-09'))
        self.assertEqual(f.end_time.hour, 12)

    def test_timestamp_with_offset_is_converted_to_utc(self):
        f = resolve_filters(request(start_date='2026-10-09T13:00:00+02:00', end_date='2026-10-09T14:00:00+02:00'))
        self.assertEqual(f.start_time.hour, 11)

    def test_bad_dates_are_bad_parameters_not_500s(self):
        for start, end in [('2026-13-01', '2026-10-20'), ('yesterday', 'today'), ('2026-10-01', '9999-12-31'),
                           ('1999-01-01', '2026-10-20'), ('2026-10-01', '2101-01-01'), ('2026-10-0', '2026-10-1x'),
                           ('2026-10-09T12:00:00Z', 'garbage-garbage')]:
            with self.subTest(start=start, end=end), self.assertRaises(BadParameter):
                resolve_filters(request(start_date=start, end_date=end))

    def test_window_must_be_forward(self):
        with self.assertRaises(BadParameter):
            resolve_filters(request(start_date='2026-10-09T12:00:00Z', end_date='2026-10-09T11:00:00Z'))

    def test_object_filters_are_checked_only_when_asked(self):
        resolve_filters(request(action='bogus'))
        with self.assertRaises(BadParameter):
            resolve_filters(request(action='bogus'), objects=True)
        self.assertEqual(resolve_filters(request(action='modify'), objects=True).action, 'modify')


class ParseBoundTests(SimpleTestCase):
    def test_end_day_is_included(self):
        self.assertEqual(parse_bound('2026-10-20', 'end_date', end=True).day, 21)
        self.assertEqual(parse_bound('2026-10-20', 'start_date', end=False).day, 20)

    def test_year_window(self):
        with self.assertRaises(BadParameter):
            parse_bound('2101-01-01', 'start_date', end=False)


class ParseBboxTests(SimpleTestCase):
    def test_valid(self):
        self.assertEqual(parse_bbox('2,48,3,49'), (2.0, 48.0, 3.0, 49.0))

    def test_past_the_world_edge_is_clamped(self):
        self.assertEqual(parse_bbox('-200,-95,200,95'), (-180.0, -90.0, 180.0, 90.0))

    def test_rejected(self):
        for raw in ['nan,nan,nan,nan', '-inf,-90,inf,90', '3,49,2,48', '1,2,3', 'a,b,c,d', '', '1,2,3,4,5']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_bbox(raw)


class PickIntervalTests(SimpleTestCase):
    def test_auto(self):
        self.assertEqual(pick_interval('2026-10-01', '2026-10-07', None), 'hour')
        self.assertEqual(pick_interval('2026-09-01', '2026-10-20', None), 'day')

    def test_boundary_is_300_hourly_points(self):
        self.assertEqual(pick_interval('2026-10-01', '2026-10-12', None), 'hour')   # 12 days = 288
        self.assertEqual(pick_interval('2026-10-01', '2026-10-13', None), 'day')    # 13 days = 312

    def test_explicit_hour_over_a_wide_range_is_refused(self):
        with self.assertRaises(ValueError):
            pick_interval('2026-01-01', '2026-10-20', 'hour')
        self.assertEqual(pick_interval('2026-01-01', '2026-10-20', 'day'), 'day')


class PaginationDepthTests(SimpleTestCase):
    def paginate(self, **query):
        return ChangesetPagination().paginate_queryset([], request(**query))

    def test_deep_pages_are_refused_before_any_query(self):
        for query in [dict(page=11, page_size=1000), dict(page=101, page_size=100), dict(page=190000, page_size=1000)]:
            with self.subTest(query=query), self.assertRaises(BadParameter):
                self.paginate(**query)

    def test_the_last_allowed_page_gets_through_to_the_normal_checks(self):
        with self.assertRaises(NotFound):   # an empty list has no page 10: DRF's own 404
            self.paginate(page=10, page_size=1000)

    def test_junk_page_numbers_keep_their_old_answer(self):
        with self.assertRaises(NotFound):
            self.paginate(page='abc')


class FillBucketsTests(SimpleTestCase):
    NOW = datetime(2026, 10, 30, 12, 30)

    def filled(self, f, interval, counts, dates, **kw):
        data = {'interval': interval, 'dates': dates, 'series': [{'name': 'x', 'counts': counts}]}
        return fill_buckets(data, f, interval, now=self.NOW, **kw)

    def test_hours_between_points_become_zeros(self):
        f = Filters(start_date='2026-10-01', end_date='2026-10-01')
        data = self.filled(f, 'hour', [5, 7], ['2026-10-01 03:00', '2026-10-01 05:00'])
        self.assertEqual(len(data['dates']), 24)
        self.assertEqual(data['series'][0]['counts'][3:6], [5, 0, 7])
        self.assertEqual(sum(data['series'][0]['counts']), 12)

    def test_days_and_the_current_bucket_ends_the_range(self):
        f = Filters(start_date='2026-10-28', end_date='2026-10-31')
        data = self.filled(f, 'day', [4], ['2026-10-29'])
        self.assertEqual(data['dates'], ['2026-10-28', '2026-10-29', '2026-10-30'])
        self.assertEqual(data['series'][0]['counts'], [0, 4, 0])
        data = self.filled(f, 'hour', [1], ['2026-10-30 12:00'])
        self.assertEqual(data['dates'][-1], '2026-10-30 12:00')

    def test_empty_series_and_object_coverage_start(self):
        f = Filters(start_date='2026-10-01', end_date='2026-10-03')
        self.assertEqual(fill_buckets({'interval': 'day', 'dates': [], 'series': []}, f, 'day', now=self.NOW)['dates'], [])
        data = self.filled(f, 'day', [3], ['2026-10-03'], first_day=date(2026, 10, 2))
        self.assertEqual(data['dates'], ['2026-10-02', '2026-10-03'])

    def test_every_series_is_aligned(self):
        f = Filters(start_date='2026-10-01', end_date='2026-10-03')
        data = {'interval': 'day', 'dates': ['2026-10-01', '2026-10-03'],
                'series': [{'name': 'a', 'counts': [1, 2]}, {'name': 'b', 'counts': [0, 9]}]}
        fill_buckets(data, f, 'day', now=self.NOW)
        self.assertEqual([s['counts'] for s in data['series']], [[1, 0, 2], [0, 0, 9]])


class LoneBoundTests(SimpleTestCase):
    def test_a_lone_end_keeps_the_end_and_starts_a_week_before(self):
        f = resolve_filters(request(end_date='2026-01-31'))
        self.assertEqual((f.start_date, f.end_date), ('2026-01-24', '2026-01-31'))

    def test_a_lone_start_keeps_the_start_and_ends_today(self):
        f = resolve_filters(request(start_date='2026-10-01'))
        self.assertEqual(f.start_date, '2026-10-01')
        self.assertGreater(f.end_date, '2026-10-01')

    def test_a_lone_timestamp_end_defaults_the_start_as_a_timestamp(self):
        f = resolve_filters(request(end_date='2026-10-09T11:00:00Z'))
        self.assertEqual(f.end_time, datetime(2026, 10, 9, 11))
        self.assertEqual(f.start_time, datetime(2026, 10, 2, 11))

    def test_a_bad_lone_end_is_a_400(self):
        with self.assertRaises(BadParameter):
            resolve_filters(request(end_date='2026-13-45'))


class ParamAliasTests(SimpleTestCase):
    def test_either_name_works_and_conflicts_are_rejected(self):
        get = lambda **q: param_with_alias(request(**q).query_params, 'contributor', 'user')
        self.assertEqual(get(contributor='Ann'), 'Ann')
        self.assertEqual(get(user='Ann'), 'Ann')
        self.assertEqual(get(contributor='Ann', user='ann'), 'Ann')
        self.assertEqual(get(), '')
        with self.assertRaises(BadParameter):
            get(contributor='Ann', user='Bob')
