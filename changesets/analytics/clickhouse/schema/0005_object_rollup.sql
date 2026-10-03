-- Daily rollup of object changes (0004's object_change_features), for the
-- API's object dimensions (group_by / dimension = action, object_type,
-- feature). Same pattern as daily_rollup (0002, see there for why
-- refreshable): queries read it up to its last day, the raw tables after.
--
-- Objects are attributed to their changeset's created_at day, like
-- changes_count everywhere else, so totals match the "objects changed" ones.
-- That date and the filter dimensions come from `changesets`: a join the
-- raw tables can't afford per query (measured: 6.9 s for a 52-day daily
-- series by action). Only changesets created on or after the first day the
-- diffs fully cover are included (the day after the earliest edit, so every
-- upload of a changeset is in the diffs); queries clamp to the same day.

-- One row per (dimension, day, name, type, action, feature). dimension
-- 'volume' (name '') holds the totals; NULL names are stored as '(none)'.
CREATE TABLE IF NOT EXISTS object_daily_rollup
(
    dimension LowCardinality(String),
    day       Date,
    name      String,
    type      Enum8('node' = 1, 'way' = 2, 'relation' = 3),
    action    Enum8('create' = 1, 'modify' = 2, 'delete' = 3),
    feature   LowCardinality(String),
    objects   UInt64
)
ENGINE = MergeTree
ORDER BY (dimension, day, name, type, action, feature);

-- Covers changesets created before yesterday (UTC) at refresh time, like
-- daily_rollup: a changeset can still receive uploads for 24 h.
CREATE MATERIALIZED VIEW IF NOT EXISTS object_daily_rollup_refresh
REFRESH EVERY 1 DAY OFFSET 6 HOUR
TO object_daily_rollup
AS SELECT
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
    GROUP BY changeset_id, type, action, feature
) AS f
INNER JOIN
(
    SELECT changeset_id, created_at, created_by_family, imagery_family, locale_family, country_code
    FROM changesets FINAL
    WHERE created_at >= (SELECT toDateTime(toDate(min(edit_time)) + 1) FROM object_changes)
      AND created_at < toDateTime(today() - 1)
) AS c USING changeset_id
ARRAY JOIN [
    ('volume', ''),
    ('editor', coalesce(c.created_by_family, '(none)')),
    ('imagery', coalesce(c.imagery_family, '(none)')),
    ('language', coalesce(c.locale_family, '(none)')),
    ('country', coalesce(c.country_code, '(none)'))
] AS d
GROUP BY dimension, day, name, type, action, feature;
