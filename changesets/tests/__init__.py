"""Tests that need no ClickHouse and no network: parsing, validation, the pure rules.

    docker compose run --rm --no-deps web python manage.py test        (deploy.sh does this)

The container's environment points at the live ClickHouse, so a test that forgot to stub
the backend would read and write production: any connection attempt fails the run instead.
"""
from unittest import mock

mock.patch('clickhouse_connect.get_client',
           side_effect=AssertionError('a test tried to reach ClickHouse: stub the backend')).start()
