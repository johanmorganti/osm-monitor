"""Precomputed daily aggregates behind the dashboard's unfiltered view, plus
the distinct filter values behind its autocomplete inputs.

Three refresh functions, two of them run automatically from the poller on
different cadences — see poll_sequences.py:

- refresh_rollups_incremental() (frequent, e.g. every couple of minutes):
  aggregates only Changeset rows inserted since the last call (tracked via
  RollupState.last_id) and merges their counts into the existing rollup
  rows with an UPSERT. Cheap regardless of table size, since `id` is the
  primary key — an index range scan, not a sequential one. Also upserts
  any newly-seen contributor/editor/imagery values into FilterValue from
  that same batch (ON CONFLICT DO NOTHING — existence only, no counting).

- refresh_rollups_reconcile() (infrequent, e.g. every few hours): corrects
  drift the incremental path leaves behind — osm_fetcher.process_sequence
  sometimes deletes and recreates a changeset with a higher changes_count
  (it grew more edits before closing). The recreated row gets a fresh id,
  so the incremental pass picks up its new contribution correctly, but the
  old contribution from the deleted row is still sitting in the rollup
  tables from an earlier pass — briefly double-counting that changeset.
  Rather than recomputing from the *entire* Changeset table like the old
  refresh_rollups() did (prohibitively slow once the table is large —
  see TODO.md), this only recomputes the last few days: OSM changesets
  close within at most a few days in practice, so drift can't exist any
  further back than that, and older days are already exactly correct from
  a prior pass. DELETE + INSERT rather than TRUNCATE, so it doesn't lock
  out readers like the old full rebuild did.

- refresh_rollups() (manual/rare — see the refresh_rollups management
  command): the original full TRUNCATE + rebuild from the *entire*
  Changeset table. No longer called automatically; kept as a deliberate,
  human-triggered escape hatch for when something broader than the recent
  window needs correcting (e.g. after a bulk import or a data-fixing
  script that touched old rows). Expect it to be slow at scale.
"""

from datetime import timedelta

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
