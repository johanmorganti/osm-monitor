"""ClickHouse writer: plain inserts into the ReplacingMergeTree `changesets` table.

No read-before-write: the table keeps, per (created_at, changeset_id), the row
with the highest changes_count (schema/0001_changesets.sql), which is exactly
the writer contract's "replace only if it grew". So this writer can't tell
created from updated from skipped; it reports every record as created.
"""
import json

from ...analytics.clickhouse.client import get_client
from .base import WriteResult

COLUMNS = (
    'changeset_id', 'created_at', 'closed_at', 'open', 'changes_count', 'user', 'user_id',
    'min_lat', 'max_lat', 'min_lon', 'max_lon', 'comments_count', 'created_by', 'created_by_family',
    'comment', 'locale', 'locale_family', 'source', 'imagery_used', 'imagery_family', 'host',
    'changesets_count', 'hashtags', 'streetcomplete_quest_type', 'review_requested',
    'remaining_tags', 'geohash', 'country_code',
)
_ARRAYS = {'imagery_used', 'hashtags'}  # absent -> [] (ClickHouse arrays can't be NULL)


def to_row(record):
    row = []
    for column in COLUMNS:
        value = record.get(column)
        if column in _ARRAYS:
            value = value or []
        elif column == 'changes_count':
            value = value or 0  # version column, not nullable
        elif column == 'remaining_tags' and value is not None:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        row.append(value)
    return row


class ClickHouseWriter:
    name = 'clickhouse'

    def write(self, records, log_extra):
        if records:
            get_client().insert('changesets', [to_row(r) for r in records], column_names=COLUMNS)
        return WriteResult(len(records), 0, 0)

    def after_backfill(self, start, end, stdout=None):
        """Nothing to refresh: no pre-computed aggregates (yet)."""
