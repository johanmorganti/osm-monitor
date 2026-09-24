"""Continuous-aggregate refresh helpers for anything that writes data
outside a CAgg's own 7-day refresh-policy window (see CLAUDE.md's
"Old-dated rows in the replication stream are normal" section) and
therefore needs an explicit `CALL refresh_continuous_aggregate` to become
visible — a deliberate backward/bulk import (`import_from_dump.py`), or a
backfill that corrects historical values already inside a CAgg's coverage
(e.g. the Organic Maps editor-family fix).

Shared rather than duplicated per call site so a future new CAgg only needs
adding to ALL_CAGG_NAMES once, instead of every backfill/import command
that needs to refresh "everything" separately remembering to update its own
copy of the list.
"""

import logging
import time
from datetime import timedelta

from django.db import connection

logger = logging.getLogger(__name__)

# Every CAgg in the schema (migrations 0020, 0023, 0033, 0041, 0043, 0045,
# 0048) — kept as one list specifically so nothing needs to remember to
# update more than one place when a new one is added.
ALL_CAGG_NAMES = [
    'cagg_volume_hourly', 'cagg_volume_daily',
    'cagg_editor_hourly', 'cagg_editor_daily',
    'cagg_imagery_hourly', 'cagg_imagery_daily',
    'cagg_locale_hourly', 'cagg_locale_daily',
    'cagg_contributor_hourly', 'cagg_contributor_daily',
    'cagg_country_hourly', 'cagg_country_daily',
    'cagg_geo_hashed_daily',
    'cagg_editor_imagery_daily', 'cagg_editor_locale_daily', 'cagg_imagery_locale_daily',
    'cagg_contributor_editor_daily', 'cagg_contributor_imagery_daily', 'cagg_contributor_locale_daily',
    'cagg_contributor_country_daily', 'cagg_country_editor_daily', 'cagg_country_imagery_daily',
    'cagg_editor_version_daily',
]


def refresh_caggs_over_range(start, end, cagg_names=ALL_CAGG_NAMES, batch_days=30, pause_seconds=0, stdout=None):
    """`CALL refresh_continuous_aggregate(cagg, batch_start, batch_end)` for
    every name in cagg_names, walking [start, end) in batch_days-sized
    chunks — never one call over the whole range: an earlier single-huge-
    range attempt on cagg_volume_hourly appeared to stall on this host (see
    docs/todo/continuous-aggregates-migration.md's history), and the
    geohash/country backfill session confirmed CALL refresh_continuous_
    aggregate over a large/dense range can trigger the same parallel-worker
    /dev/shm exhaustion crash `docker-compose.yml`'s `shm_size` comment
    documents for VACUUM ANALYZE — `max_parallel_workers_per_gather = 0`
    below avoids it, same fix used there. That guard is now also the role
    default (db/init/03-role-parallel-workers.sh) so every connection gets
    it, not just this one — but confirmed 2026-09-22 it isn't sufficient
    alone: the country CAgg backfill still crashed Postgres mid-run even
    with the role default in place, on cagg_country_hourly (a cheap,
    low-cardinality CAgg, not the expensive contributor-crossed one) — this
    host was simply out of headroom (~150MB free RAM, ~900MB swapped in
    steady state all session) after ~20 minutes of continuous back-to-back
    CALLs, not any one query. `pause_seconds` (default 0 — off for existing
    callers) sleeps between date-batches, not between individual CALLs
    within one, to give the host a real recovery window under sustained
    pressure like that, at the cost of a much longer wall-clock backfill.

    Each CALL is its own statement — refresh_continuous_aggregate manages
    its own transaction internally and cannot run inside one (`ERROR:
    refresh_continuous_aggregate() cannot run inside a transaction
    block`) — so a crash mid-run only loses whatever (cagg, batch) pair was
    in flight; safe to just call this again over the same range afterward,
    since refreshing an already-current range is a fast no-op.

    Caller must have already `SET statement_timeout = 0` on this
    connection — a dense month's refresh can legitimately run past the
    120s role default (see CLAUDE.md's "Statement timeout" section)."""
    with connection.cursor() as cursor:
        cursor.execute("SET max_parallel_workers_per_gather = 0")

    # Floor/ceil to midnight UTC before walking — confirmed empirically
    # (2026-09-20) that `refresh_continuous_aggregate` silently produces NO
    # row at all for a bucket when the window boundary falls *inside* it
    # rather than exactly on its edge, instead of refreshing the bucket in
    # full: a window starting at 10:00 on a day with exactly one changeset
    # left that whole day's bucket completely absent from the CAgg, while
    # the immediately following (fully-contained) days materialized fine.
    # Flooring the start and ceiling the end to a day boundary guarantees
    # every bucket this call touches is fully contained in the window —
    # safe for hourly CAggs too, since a day boundary is always also an
    # hour boundary. Every other chunked-refresh call in this project
    # happened to already pass midnight-aligned boundaries (chunk edges,
    # or manually constructed `datetime(y, m, d)` values) and was never
    # affected — this matters specifically for a caller like
    # import_from_dump.py that passes the *exact* timestamp of the
    # earliest/latest row actually imported.
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if end != end.replace(hour=0, minute=0, second=0, microsecond=0):
        end = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    batch_delta = timedelta(days=batch_days)
    cursor_start = start
    while cursor_start < end:
        batch_end = min(cursor_start + batch_delta, end)
        for cagg in cagg_names:
            msg = f'{cagg}: refreshing {cursor_start.date()}..{batch_end.date()}'
            if stdout:
                stdout.write(msg)
            batch_start_time = time.monotonic()
            with connection.cursor() as cursor:
                cursor.execute("CALL refresh_continuous_aggregate(%s, %s, %s)", [cagg, cursor_start, batch_end])
            elapsed = time.monotonic() - batch_start_time
            logger.info(
                "CAgg refresh batch",
                extra={
                    'osm.cagg_refresh.name': cagg,
                    'osm.cagg_refresh.range_start': str(cursor_start.date()),
                    'osm.cagg_refresh.range_end': str(batch_end.date()),
                    'osm.cagg_refresh.seconds': round(elapsed, 1),
                },
            )
        cursor_start = batch_end
        if pause_seconds and cursor_start < end:
            if stdout:
                stdout.write(f'Pausing {pause_seconds}s before next batch...')
            time.sleep(pause_seconds)
