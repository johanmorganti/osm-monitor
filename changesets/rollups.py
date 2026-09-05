"""Precomputed daily aggregates behind the dashboard's unfiltered view.

Rebuilt in full on every refresh (TRUNCATE + INSERT...SELECT, one transaction)
rather than updated incrementally — there's no per-write delta bookkeeping to
get wrong or drift out of sync with the Changeset table, at the cost of the
refresh interval's worth of staleness. Postgres aggregates the full table in
low single-digit seconds, so refreshing every couple of minutes from the
poller keeps that staleness small.
"""

from django.db import connection

_REFRESH_SQL = """
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


def refresh_rollups():
    """Rebuild DailyVolume and DailyBreakdown from the Changeset table.
    Readers on Postgres's default isolation see either the pre- or
    post-refresh state, never an empty intermediate one, since TRUNCATE is
    transactional there."""
    with connection.cursor() as cursor:
        cursor.execute(_REFRESH_SQL)
