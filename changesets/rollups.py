"""Precomputed daily aggregates behind the dashboard's unfiltered view.

Two refresh paths, run on different cadences from the poller:

- refresh_rollups_incremental() (frequent, e.g. every couple of minutes):
  aggregates only Changeset rows inserted since the last call (tracked via
  RollupState.last_id) and merges their counts into the existing rollup
  rows with an UPSERT. Cheap regardless of table size, since `id` is the
  primary key — an index range scan, not a sequential one.

- refresh_rollups() (infrequent, e.g. hourly): the original full
  TRUNCATE + rebuild from the whole Changeset table. This exists because
  the incremental path can drift: osm_fetcher.process_sequence sometimes
  deletes and recreates a changeset with a higher changes_count (it grew
  more edits before closing). The recreated row gets a fresh id, so the
  incremental pass picks up its new contribution correctly, but the old
  contribution from the deleted row is still sitting in the rollup tables
  from an earlier pass — briefly double-counting that changeset. The full
  rebuild recomputes straight from current Changeset rows, so it always
  corrects this drift; the incremental path just keeps the dashboard
  fresh in between.
"""

from django.db import connection
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


def refresh_rollups_incremental():
    """Merge only Changeset rows inserted since the last call into the
    rollup tables. See module docstring for the double-counting caveat
    that refresh_rollups() corrects."""
    state, _ = RollupState.objects.get_or_create(pk=1)

    with connection.cursor() as cursor:
        cursor.execute(_INCREMENTAL_SQL, {'last_id': state.last_id})
        cursor.execute("SELECT COALESCE(MAX(id), %s) FROM changesets_changeset", [state.last_id])
        new_last_id = cursor.fetchone()[0]

    if new_last_id != state.last_id:
        state.last_id = new_last_id
        state.save(update_fields=['last_id'])


def refresh_rollups():
    """Rebuild DailyVolume and DailyBreakdown from the Changeset table.
    Readers on Postgres's default isolation see either the pre- or
    post-refresh state, never an empty intermediate one, since TRUNCATE is
    transactional there."""
    with connection.cursor() as cursor:
        cursor.execute(_FULL_REFRESH_SQL)
