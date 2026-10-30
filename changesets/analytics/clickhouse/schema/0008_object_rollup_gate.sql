-- object_daily_rollup_append (0005) with a completeness gate: it adds the oldest
-- missing day only once that day's objects in the diffs add up to its
-- changesets' changes_count (the check object-changes.md describes, equal on
-- every normal day). Without it, a day built while the diffs or changesets for
-- it were still being loaded was stored short and never rebuilt: 2026-10-18
-- held 127 objects instead of 4,004,637, written by the 2026-10-26 16:00
-- refresh during the backfill after the host rebuild.
--
-- While the oldest missing day is incomplete, nothing is appended (the later
-- days wait for it): the next refresh tries again, and check_object_rollup
-- names the day it is waiting on. 0.999, not equality: a changeset whose
-- final update we missed can differ by a few objects (6 in 199M, see
-- object-changes.md).
--
-- MODIFY QUERY keeps the schedule and the stored rows and starts no refresh,
-- so applying it on every deploy is harmless. 0005 stays as created.
ALTER TABLE object_daily_rollup_append MODIFY QUERY
WITH
    (SELECT toDate(min(edit_time)) + 1 FROM object_changes) AS first_day,
    (
        SELECT min(d) FROM
        (
            SELECT arrayJoin(arrayMap(i -> first_day + i, range(toUInt32(greatest(today() - 1, first_day) - first_day)))) AS d
        )
        WHERE d NOT IN (SELECT DISTINCT day FROM object_daily_rollup WHERE dimension = 'volume')
    ) AS candidate,
    (
        SELECT sum(changes_count) FROM changesets FINAL
        WHERE created_at >= toDateTime(candidate) AND created_at < toDateTime(candidate + 1)
    ) AS expected,
    (
        SELECT sum(count) FROM object_change_features FINAL
        WHERE changeset_id IN (
            SELECT changeset_id FROM changesets
            WHERE created_at >= toDateTime(candidate) AND created_at < toDateTime(candidate + 1))
    ) AS got,
    if(expected > 0 AND got >= expected * 0.999, candidate, NULL) AS target
SELECT
    d.1 AS dimension,
    toDate(c.created_at) AS day,
    d.2 AS name,
    f.type AS type,
    f.action AS action,
    f.feature AS feature,
    sum(f.n) AS objects
FROM
(
    SELECT changeset_id, type, action, feature, sum(count) AS n
    FROM object_change_features FINAL
    WHERE changeset_id IN (
        SELECT changeset_id FROM changesets
        WHERE created_at >= toDateTime(target) AND created_at < toDateTime(target + 1))
    GROUP BY changeset_id, type, action, feature
) AS f
INNER JOIN
(
    SELECT changeset_id, created_at, created_by_family, imagery_family, locale_family, country_code
    FROM changesets FINAL
    WHERE created_at >= toDateTime(target) AND created_at < toDateTime(target + 1)
) AS c USING changeset_id
ARRAY JOIN [
    ('volume', ''),
    ('editor', coalesce(c.created_by_family, '(none)')),
    ('imagery', coalesce(c.imagery_family, '(none)')),
    ('language', coalesce(c.locale_family, '(none)')),
    ('country', coalesce(c.country_code, '(none)'))
] AS d
GROUP BY dimension, day, name, type, action, feature
SETTINGS max_insert_threads = 1;
