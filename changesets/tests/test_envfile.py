import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from osm_changeset_api.envfile import read_env_file


class EnvFileTests(SimpleTestCase):
    def read(self, text, env=None):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / '.env'
            path.write_text(text)
            with mock.patch.dict(os.environ, env or {}, clear=False):
                return read_env_file(path)

    def test_missing_file_is_empty(self):
        self.assertEqual(read_env_file('/nonexistent/.env'), {})

    def test_plain_pairs_comments_and_export(self):
        self.assertEqual(self.read('A=1\n# comment\n\nexport B = two \nC=\n'), {'A': '1', 'B': 'two', 'C': ''})

    def test_inline_comment_only_on_unquoted_values(self):
        values = self.read('A=1 # note\nB="x # y"\nC=\'p # q\'\nD=a#b\n')
        self.assertEqual(values, {'A': '1', 'B': 'x # y', 'C': 'p # q', 'D': 'a#b'})

    def test_quotes_and_escapes(self):
        values = self.read('A="a\\nb"\nB=\'a\\nb\'\nC="say \\"hi\\""\n')
        self.assertEqual(values, {'A': 'a\nb', 'B': 'a\\nb', 'C': 'say "hi"'})

    def test_expansion_from_earlier_lines_the_environment_and_defaults(self):
        values = self.read('A=/data\nB=${A}/x\nC=${NOPE:-fallback}\nD=${FROM_ENV}\nE=${NOPE}\n', {'FROM_ENV': 'env'})
        self.assertEqual(values, {'A': '/data', 'B': '/data/x', 'C': 'fallback', 'D': 'env', 'E': ''})

    def test_lines_that_are_not_assignments_are_ignored(self):
        self.assertEqual(self.read('not an assignment\n  # indented comment\nA=1\n'), {'A': '1'})

    def test_env_example_parses(self):
        # The file operators copy: every active line must read back as written.
        values = read_env_file(Path(__file__).resolve().parents[2] / 'env.example')
        self.assertIn('SECRET_KEY', values)


class ClickHouseCredentialsTests(SimpleTestCase):
    OWNER = {'CLICKHOUSE_USER': 'owner', 'CLICKHOUSE_PASSWORD': 'p1'}

    def creds(self, **extra):
        from osm_changeset_api.settings import clickhouse_credentials
        return clickhouse_credentials({**self.OWNER, **extra})

    def test_everything_but_web_uses_the_owner_login(self):
        self.assertEqual(self.creds(), ('owner', 'p1'))
        self.assertEqual(self.creds(CLICKHOUSE_WEB_PASSWORD='w'), ('owner', 'p1'))   # not flagged read-only

    def test_web_uses_the_select_only_login(self):
        self.assertEqual(self.creds(CLICKHOUSE_READ_ONLY='1', CLICKHOUSE_WEB_PASSWORD='w'), ('osm_monitor_web', 'w'))
        self.assertEqual(self.creds(CLICKHOUSE_READ_ONLY='1', CLICKHOUSE_WEB_PASSWORD='w', CLICKHOUSE_WEB_USER='ro'), ('ro', 'w'))

    def test_web_without_the_password_keeps_working_on_the_owner_login(self):
        self.assertEqual(self.creds(CLICKHOUSE_READ_ONLY='1'), ('owner', 'p1'))
