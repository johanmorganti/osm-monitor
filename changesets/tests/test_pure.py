from django.test import SimpleTestCase

from changesets.api.sizes import distribution, quantile
from changesets.geo import (geohash_bbox_cover, geohash_cell_size_degrees, geohash_decode_center,
                            geohash_encode, geohash_precision_for_bbox)


class GeohashTests(SimpleTestCase):
    def test_known_values(self):
        self.assertEqual(geohash_encode(57.64911, 10.40744, 11), 'u4pruydqqvj')
        self.assertEqual(geohash_encode(48.8566, 2.3522, 5), 'u09tv')

    def test_decode_is_inside_the_cell(self):
        lat, lon = geohash_decode_center(geohash_encode(48.8566, 2.3522, 6))
        lat_size, lon_size = geohash_cell_size_degrees(6)
        self.assertLess(abs(lat - 48.8566), lat_size)
        self.assertLess(abs(lon - 2.3522), lon_size)

    def test_cell_sizes_shrink_with_precision(self):
        sizes = [geohash_cell_size_degrees(p) for p in range(1, 7)]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_precision_grows_as_the_viewport_shrinks(self):
        world = geohash_precision_for_bbox(-90, -180, 90, 180)
        city = geohash_precision_for_bbox(48.8, 2.2, 48.9, 2.5)
        self.assertEqual(world, 4)   # FINE_MIN_PRECISION
        self.assertGreater(city, world)

    def test_cover_contains_the_points_and_is_bounded(self):
        prefixes = geohash_bbox_cover(48.8, 2.2, 48.9, 2.5)
        self.assertTrue(prefixes)
        self.assertLessEqual(len(prefixes), 64)
        self.assertTrue(any(geohash_encode(48.85, 2.35, len(p)).startswith(p) for p in prefixes))


class SizeDistributionTests(SimpleTestCase):
    COUNTS = [(1, 50), (2, 30), (10, 15), (1000, 5)]   # sorted by size, 100 changesets

    def test_quantile_is_the_smallest_size_reaching_the_level(self):
        self.assertEqual(quantile(self.COUNTS, 100, 0.5), 1)
        self.assertEqual(quantile(self.COUNTS, 100, 0.51), 2)
        self.assertEqual(quantile(self.COUNTS, 100, 0.95), 10)
        self.assertEqual(quantile(self.COUNTS, 100, 1.0), 1000)

    def test_distribution_totals(self):
        d = distribution(self.COUNTS)
        self.assertEqual((d['total_changesets'], d['total_objects']), (100, 50 + 60 + 150 + 5000))
        self.assertEqual(sum(b['changesets'] for b in d['buckets']), 100)
        self.assertEqual(sum(b['objects'] for b in d['buckets']), d['total_objects'])
        self.assertEqual(d['percentiles']['max'], 1000)
        # The largest 1% (one changeset of 1000 objects) holds 5000/5260 ... of the objects
        self.assertAlmostEqual(d['top_1pct_objects_share'], 1000 / 5260, places=4)

    def test_empty(self):
        d = distribution([])
        self.assertEqual((d['total_changesets'], d['avg_objects'], d['top_1pct_objects_share']), (0, 0, None))


class ClickHouseGuardTests(SimpleTestCase):
    def test_a_test_cannot_reach_clickhouse(self):
        from changesets.analytics.clickhouse.client import get_client
        with self.assertRaises(AssertionError):
            get_client()
