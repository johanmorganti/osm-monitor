import io
import xml.etree.ElementTree as ET

from django.test import SimpleTestCase

from changesets.ingest.osmchange import UNKNOWN, UNTAGGED, feature_of, iter_versions
from changesets.osm_fetcher import _parse_changeset_element


def changeset(tags=(), **attrs):
    attrs = {'id': '1', 'created_at': '2026-10-20T10:00:00Z', 'open': 'false', 'changes_count': '5',
             'closed_at': '2026-10-20T10:01:00Z', 'uid': '7', 'user': 'someone', **attrs}
    el = ET.Element('changeset', attrs)
    for key, value in tags:
        ET.SubElement(el, 'tag', {'k': key, 'v': value})
    return el


def parse(*tags, **attrs):
    return _parse_changeset_element(changeset(tags, **attrs), {})


class EditorFamilyTests(SimpleTestCase):
    def family(self, created_by):
        return parse(('created_by', created_by))['created_by_family']

    def test_known_editors(self):
        for created_by, expected in [
            ('JOSM/1.5 (19017 en)', 'JOSM'), ('iD 2.30.1', 'iD'), ('Go Map!! 2.9.2', 'Go Map!!'),
            ('StreetComplete 51.0', 'StreetComplete'), ('OsmAnd~ 4.8', 'OsmAnd'), ('Osm Go! 1.0', 'Osm Go!'),
            ('Rapid 2.1.0', 'Rapid'), ('MAPS.ME ios 10.1', 'MAPS.ME'), ('Vespucci/20.1 (x)', 'Vespucci'),
        ]:
            with self.subTest(created_by=created_by):
                self.assertEqual(self.family(created_by), expected)

    def test_renamed_families(self):
        for created_by, expected in [('AED map', 'AED Map'), ('Abakus 1', 'StrazakOSM'), ('Every Door 4', 'Every Door'),
                                     ('Organic Maps 2024', 'Organic Maps'), ('Votre app', 'Ubiflow')]:
            with self.subTest(created_by=created_by):
                self.assertEqual(self.family(created_by), expected)

    def test_url_editors_keep_the_url(self):
        self.assertEqual(self.family('https://example.org/editor v2'), 'https://example.org/editor v2')

    def test_empty_is_other(self):
        self.assertEqual(self.family(''), 'Other')

    def test_josm_anywhere_in_the_tag(self):
        self.assertEqual(self.family('Custom JOSM build'), 'JOSM')


class ChangesetFieldsTests(SimpleTestCase):
    def test_typed_attributes(self):
        row = parse(open='true', min_lat='1.5', max_lat='2.5', min_lon='3.5', max_lon='4.5', comments_count='2')
        self.assertIs(row['open'], True)
        self.assertEqual((row['changeset_id'], row['changes_count'], row['user_id'], row['comments_count']), (1, 5, 7, 2))
        self.assertEqual((row['min_lat'], row['max_lon']), (1.5, 4.5))
        self.assertEqual(row['created_at'].tzinfo.utcoffset(None).total_seconds(), 0)

    def test_osm_api_names_for_the_count(self):
        el = changeset(); del el.attrib['changes_count']; el.set('num_changes', '9')
        self.assertEqual(_parse_changeset_element(el, {})['changes_count'], 9)

    def test_hashtags_drop_the_hash_and_empty_parts(self):
        self.assertEqual(parse(('hashtags', '#one;#two;;three'))['hashtags'], ['one', 'two', 'three'])

    def test_locale_family(self):
        self.assertEqual(parse(('locale', 'fr_FR'))['locale_family'], 'FR')
        self.assertEqual(parse(('locale', 'pt-BR'))['locale_family'], 'PT')

    def test_changesets_count_garbage_is_null(self):
        for value in ['abc', '-1', str(2**31)]:
            with self.subTest(value=value):
                self.assertIsNone(parse(('changesets_count', value))['changesets_count'])
        self.assertEqual(parse(('changesets_count', '73'))['changesets_count'], 73)

    def test_imagery_families(self):
        row = parse(('imagery_used', 'Bing aerial imagery; Maxar Premium'))
        self.assertEqual(row['imagery_used'], ['Bing aerial imagery', 'Maxar Premium'])
        self.assertEqual(row['imagery_family'], 'Bing')
        self.assertEqual(parse(('imagery_used', 'https://tiles.example.com/x/{z}'))['imagery_family'], 'tiles.example.com')
        for none in ['None', 'unknown', 'N/A']:
            with self.subTest(none=none):
                self.assertIsNone(parse(('imagery_used', none))['imagery_family'])

    def test_other_tags_are_kept(self):
        self.assertEqual(parse(('building', 'yes'))['remaining_tags'], {'building': 'yes'})


class FeatureTests(SimpleTestCase):
    def test_specific_poi_keys_win(self):
        self.assertEqual(feature_of('create', {'building': 'yes', 'amenity': 'school'}), 'amenity')
        self.assertEqual(feature_of('modify', {'highway': 'residential', 'name': 'x'}), 'highway')

    def test_deletes_have_no_tags(self):
        self.assertEqual(feature_of('delete', {'building': 'yes'}), UNKNOWN)

    def test_untagged_and_noise(self):
        self.assertEqual(feature_of('create', {}), UNTAGGED)
        self.assertEqual(feature_of('create', {'created_by': 'JOSM', 'source': 'survey'}), UNTAGGED)

    def test_addr_multipolygon_other(self):
        self.assertEqual(feature_of('modify', {'addr:street': 'x', 'addr:housenumber': '1'}), 'addr')
        self.assertEqual(feature_of('modify', {'type': 'multipolygon'}), 'multipolygon')
        self.assertEqual(feature_of('modify', {'name': 'x'}), 'other')


OSC = b"""<?xml version="1.0"?>
<osmChange version="0.6">
  <create>
    <node id="1" version="1" timestamp="2026-10-20T10:00:00Z" uid="7" user="a" changeset="100" lat="48.5" lon="2.25">
      <tag k="amenity" v="cafe"/>
    </node>
    <way id="2" version="3" timestamp="2026-10-20T10:00:01Z" uid="7" user="a" changeset="100">
      <nd ref="1"/><nd ref="5"/><tag k="highway" v="path"/>
    </way>
  </create>
  <modify>
    <relation id="3" version="2" timestamp="2026-10-20T10:00:02Z" uid="8" user="b" changeset="101">
      <member type="way" ref="2" role="outer"/><tag k="type" v="multipolygon"/>
    </relation>
  </modify>
  <delete>
    <node id="4" version="5" timestamp="2026-10-20T10:00:03Z" uid="8" user="b" changeset="101" lat="1" lon="2"/>
  </delete>
</osmChange>"""


class OsmChangeParserTests(SimpleTestCase):
    def versions(self):
        return list(iter_versions(io.BytesIO(OSC)))

    def test_every_object_version_in_file_order(self):
        self.assertEqual([(v.type, v.id, v.action) for v in self.versions()],
                         [('node', 1, 'create'), ('way', 2, 'create'), ('relation', 3, 'modify'), ('node', 4, 'delete')])

    def test_fields(self):
        node, way, relation, deleted = self.versions()
        self.assertEqual((node.lat, node.lon), (485000000, 22500000))   # degrees x 1e7
        self.assertEqual((node.feature, node.changeset_id, node.version, node.user), ('amenity', 100, 1, 'a'))
        self.assertEqual((way.node_refs, way.feature), ([1, 5], 'highway'))
        self.assertEqual((relation.members, relation.feature), ([('way', 2, 'outer')], 'multipolygon'))
        self.assertEqual(deleted.feature, UNKNOWN)
        self.assertEqual(node.timestamp.isoformat(), '2026-10-20T10:00:00+00:00')
