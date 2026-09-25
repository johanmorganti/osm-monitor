"""Precomputed daily aggregates behind the dashboard's unfiltered view
(DailyVolume/DailyBreakdown), plus the distinct filter values behind its
autocomplete inputs (FilterValue) — historically refreshed together since
both were derived from the same Changeset rows, but no longer.

**As of 2026-09-18, only FilterValue is refreshed automatically.**
DailyVolume/DailyBreakdown have no reader anywhere in the codebase — every
view that used to query them (TimeseriesView/SummaryView/ToplistView) moved
to the TimescaleDB continuous aggregates (`cagg_*`, see migration 0020
onward) — yet the poller kept rebuilding them every couple of minutes via
refresh_rollups_incremental()'s `WHERE id > watermark` scan. That scan can't
use chunk exclusion (`id` isn't the hypertable's partitioning column), so it
probed the `id` index on all 141 chunks every single call — confirmed via
pg_stat_statements as ~15% of this host's total DB time in a 20-minute
sample window, for output nothing reads. See `docs/todo/continuous-aggregates-migration.md`
for the measurement and the follow-up steps (dropping the tables/columns
themselves, a bigger migration, deliberately not done in this pass).

refresh_filter_values_incremental() (frequent, run from poll_sequences.py):
aggregates only Changeset rows inserted since the last call (tracked via
RollupState.last_id — kept as FilterValue's watermark; the name predates
this file being scoped down to just this one job) and upserts any
newly-seen contributor/editor/imagery values into FilterValue (ON CONFLICT
DO NOTHING — existence only, no counting, so unlike the old DailyVolume/
DailyBreakdown merge there's no double-counting risk and thus no
"reconcile" pass needed for this one). `country` deliberately isn't part
of this incremental job — unlike contributor/editor/imagery (open-ended,
discovered incrementally from live data), the full set of possible
country_code values is already fixed and known ahead of time (the same
~238-row country_boundaries table the centroid-assignment trigger itself
draws from — see migration 0031/0032), so FilterValue's country rows are
just seeded from that table once (see the seed_country_filter_values
management command) rather than watermark-tracked.

refresh_rollups_incremental() / refresh_rollups_reconcile() / refresh_
rollups() (DailyVolume/DailyBreakdown, the old three-function design
described in this file's git history) are kept, callable, but **no longer
invoked by the poller** — see poll_sequences.py. Retained only as a
human-triggered escape hatch (via the refresh_rollups management command)
until the follow-up migration mentioned above removes them for good;
don't add a new automatic caller of these three without re-reading
`docs/todo/continuous-aggregates-migration.md` first.
"""

from datetime import datetime, timedelta

from django.db import connection
from django.utils import timezone

from .models import RollupState

_INCREMENTAL_SQL = """
INSERT INTO changesets_dailyvolume (date, hour, count, changes_sum)
SELECT created_at::date, EXTRACT(HOUR FROM created_at)::int,
       COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND id > %(last_id)s
GROUP BY 1, 2
ON CONFLICT (date, hour) DO UPDATE SET
    count = changesets_dailyvolume.count + EXCLUDED.count,
    changes_sum = changesets_dailyvolume.changes_sum + EXCLUDED.changes_sum;

INSERT INTO changesets_dailybreakdown (date, category, name, count, changes_sum)
SELECT created_at::date, 'editor', created_by_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND created_by_family IS NOT NULL AND id > %(last_id)s
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'imagery', imagery_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND imagery_family IS NOT NULL AND id > %(last_id)s
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'locale', locale_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND locale_family IS NOT NULL AND id > %(last_id)s
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'contributor', "user", COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND "user" IS NOT NULL AND id > %(last_id)s
GROUP BY 1, 3
ON CONFLICT (date, category, name) DO UPDATE SET
    count = changesets_dailybreakdown.count + EXCLUDED.count,
    changes_sum = changesets_dailybreakdown.changes_sum + EXCLUDED.changes_sum;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'contributor', "user" FROM changesets_changeset
WHERE "user" IS NOT NULL AND id > %(last_id)s
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'editor', created_by_family FROM changesets_changeset
WHERE created_by_family IS NOT NULL AND id > %(last_id)s
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'imagery', imagery_family FROM changesets_changeset
WHERE imagery_family IS NOT NULL AND id > %(last_id)s
ON CONFLICT (field, value) DO NOTHING;
"""

# Same three FilterValue upserts as _INCREMENTAL_SQL above, standalone —
# this is the only one of the two still called automatically (see module
# docstring / `docs/todo/continuous-aggregates-migration.md`). ON CONFLICT DO NOTHING means
# existence only, no counting, so — unlike DailyVolume/DailyBreakdown's
# ON CONFLICT DO UPDATE merge — a changeset that gets deleted and
# recreated with a new id can never double-count here; there is nothing
# for a "reconcile" pass to correct, so this has no counterpart to
# refresh_rollups_reconcile().
_FILTER_VALUES_INCREMENTAL_SQL = """
INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'contributor', "user" FROM changesets_changeset
WHERE "user" IS NOT NULL AND created_at >= %(last_created_at)s
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'editor', created_by_family FROM changesets_changeset
WHERE created_by_family IS NOT NULL AND created_at >= %(last_created_at)s
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'imagery', imagery_family FROM changesets_changeset
WHERE imagery_family IS NOT NULL AND created_at >= %(last_created_at)s
ON CONFLICT (field, value) DO NOTHING;
"""

# Same three upserts again, bounded on both ends instead of watermarked —
# for import_from_dump.py, which cannot use the watermarked version above:
# that command's whole purpose is a *backward* historical import (data
# older than whatever's already been live-polled), and `created_at >=
# last_created_at` would silently skip every row of it, since the
# watermark tracks the live poller's own forward progress, not "everything
# ever imported." Bounded on `created_at` (the partitioning column) on both
# sides instead, so a re-run over an already-covered range is still cheap
# (chunk-excluded) even though it isn't watermarked.
_FILTER_VALUES_RANGE_SQL = """
INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'contributor', "user" FROM changesets_changeset
WHERE "user" IS NOT NULL AND created_at >= %(start)s AND created_at < %(end)s
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'editor', created_by_family FROM changesets_changeset
WHERE created_by_family IS NOT NULL AND created_at >= %(start)s AND created_at < %(end)s
ON CONFLICT (field, value) DO NOTHING;

INSERT INTO changesets_filtervalue (field, value)
SELECT DISTINCT 'imagery', imagery_family FROM changesets_changeset
WHERE imagery_family IS NOT NULL AND created_at >= %(start)s AND created_at < %(end)s
ON CONFLICT (field, value) DO NOTHING;
"""

_FULL_REFRESH_SQL = """
TRUNCATE changesets_dailyvolume;
INSERT INTO changesets_dailyvolume (date, hour, count, changes_sum)
SELECT created_at::date, EXTRACT(HOUR FROM created_at)::int,
       COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL
GROUP BY 1, 2;

TRUNCATE changesets_dailybreakdown;
INSERT INTO changesets_dailybreakdown (date, category, name, count, changes_sum)
SELECT created_at::date, 'editor', created_by_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND created_by_family IS NOT NULL
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'imagery', imagery_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND imagery_family IS NOT NULL
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'locale', locale_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND locale_family IS NOT NULL
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'contributor', "user", COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at IS NOT NULL AND "user" IS NOT NULL
GROUP BY 1, 3;
"""

_RECONCILE_SQL = """
DELETE FROM changesets_dailyvolume WHERE date >= %(cutoff_date)s;
INSERT INTO changesets_dailyvolume (date, hour, count, changes_sum)
SELECT created_at::date, EXTRACT(HOUR FROM created_at)::int,
       COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at >= %(cutoff_dt)s
GROUP BY 1, 2;

DELETE FROM changesets_dailybreakdown WHERE date >= %(cutoff_date)s;
INSERT INTO changesets_dailybreakdown (date, category, name, count, changes_sum)
SELECT created_at::date, 'editor', created_by_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at >= %(cutoff_dt)s AND created_by_family IS NOT NULL
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'imagery', imagery_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at >= %(cutoff_dt)s AND imagery_family IS NOT NULL
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'locale', locale_family, COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at >= %(cutoff_dt)s AND locale_family IS NOT NULL
GROUP BY 1, 3
UNION ALL
SELECT created_at::date, 'contributor', "user", COUNT(*), COALESCE(SUM(changes_count), 0)
FROM changesets_changeset
WHERE created_at >= %(cutoff_dt)s AND "user" IS NOT NULL
GROUP BY 1, 3;
"""


def refresh_filter_values_incremental():
    """Upsert FilterValue rows for any contributor/editor/imagery value
    seen since the last call — the only automatic refresh the poller still
    runs (see module docstring). Watermarked on `created_at`
    (RollupState.last_created_at), not `id`: `created_at` is the
    hypertable's partitioning column, so TimescaleDB can chunk-exclude down
    to just the recent chunk(s) a live-polling batch could possibly touch,
    unlike the old `id > watermark` scan this replaces (confirmed cause of
    ~15% of this host's DB time for output nothing read — see
    `docs/todo/continuous-aggregates-migration.md`).

    `created_at >= watermark` (inclusive), not `>`: ties at the exact
    watermark instant could otherwise be skipped if they land in a later
    poll than the row that set the watermark. Safe to double-scan that
    instant — INSERT ... ON CONFLICT DO NOTHING makes re-seeing an already-
    known value a no-op, not a correctness issue."""
    state, _ = RollupState.objects.get_or_create(pk=1)

    with connection.cursor() as cursor:
        params = {'last_created_at': state.last_created_at or datetime.min.replace(tzinfo=timezone.utc)}
        cursor.execute(_FILTER_VALUES_INCREMENTAL_SQL, params)
        cursor.execute("SELECT MAX(created_at) FROM changesets_changeset WHERE created_at >= %(last_created_at)s", params)
        (new_watermark,) = cursor.fetchone()

    if new_watermark is not None and new_watermark != state.last_created_at:
        state.last_created_at = new_watermark
        state.save(update_fields=['last_created_at'])


def refresh_filter_values_over_range(start, end):
    """Upsert FilterValue rows for any contributor/editor/imagery value
    within [start, end) — for import_from_dump.py, not the live poller (see
    _FILTER_VALUES_RANGE_SQL for why the watermarked
    refresh_filter_values_incremental() is wrong for that command's actual
    use case). Does not touch or read RollupState.last_created_at — that
    watermark belongs to the live poller's forward progress and must not be
    moved backward (or at all) by a historical import."""
    with connection.cursor() as cursor:
        cursor.execute(_FILTER_VALUES_RANGE_SQL, {'start': start, 'end': end})


def refresh_rollups_incremental():
    """Merge only Changeset rows inserted since the last call into the
    rollup tables. See module docstring for the double-counting caveat
    that refresh_rollups_reconcile() corrects."""
    state, _ = RollupState.objects.get_or_create(pk=1)

    with connection.cursor() as cursor:
        cursor.execute(_INCREMENTAL_SQL, {'last_id': state.last_id})
        cursor.execute("SELECT COALESCE(MAX(id), %s) FROM changesets_changeset", [state.last_id])
        new_last_id = cursor.fetchone()[0]

    if new_last_id != state.last_id:
        state.last_id = new_last_id
        state.save(update_fields=['last_id'])


def refresh_rollups_reconcile(days=3):
    """Recompute DailyVolume/DailyBreakdown for just the last `days` days
    and replace those rows (DELETE + INSERT, not TRUNCATE — doesn't lock
    out readers). See module docstring for why a bounded recent window is
    enough to correct the incremental path's drift, without the full
    table scan refresh_rollups() needs."""
    now = timezone.now()
    cutoff_dt = now - timedelta(days=days)
    with connection.cursor() as cursor:
        cursor.execute(_RECONCILE_SQL, {'cutoff_dt': cutoff_dt, 'cutoff_date': cutoff_dt.date()})


def refresh_rollups():
    """Rebuild DailyVolume and DailyBreakdown from the Changeset table.
    Readers on Postgres's default isolation see either the pre- or
    post-refresh state, never an empty intermediate one, since TRUNCATE is
    transactional there."""
    with connection.cursor() as cursor:
        cursor.execute(_FULL_REFRESH_SQL)
