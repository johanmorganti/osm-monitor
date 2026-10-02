-- Pre-aggregation, added where measurements asked for it (daily rollups for
-- summary / timeseries / toplist, and autocomplete): see the backend's
-- docstring for which questions read these.
--
-- Refreshable materialized views, deliberately not incremental ones: an
-- incremental MV runs on every insert, and `changesets` receives new
-- versions of existing changesets (a changeset that grew, one that
-- resurfaced through a comment), which it would count twice; the base table
-- only deduplicates them later, in a merge. A refresh recomputes the whole
-- rollup from the deduplicated data (FINAL) and swaps it in atomically:
-- exact, and it also picks up old changesets that arrived late (which
-- Timescale's continuous aggregates never materialize). ~94 s for full
-- history, so once a day; queries read the rollup up to its last day and the
-- raw table after that.

-- One row per (dimension, day, name). dimension 'volume' (name '') holds the
-- daily totals; NULL names are stored as '(none)'; 'editor_version' names are
-- "family\0version".
CREATE TABLE IF NOT EXISTS daily_rollup
(
    dimension  LowCardinality(String),
    day        Date,
    name       String,
    changesets UInt64,
    objects    UInt64
)
ENGINE = MergeTree
ORDER BY (dimension, day, name);

-- Covers days before yesterday (UTC) at refresh time: a changeset can still
-- grow for up to 24 h after creation, so the last two days stay raw.
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_rollup_refresh
REFRESH EVERY 1 DAY OFFSET 3 HOUR
TO daily_rollup
AS SELECT
    d.1 AS dimension,
    toDate(created_at) AS day,
    d.2 AS name,
    count() AS changesets,
    sum(changes_count) AS objects
FROM changesets FINAL
ARRAY JOIN [
    ('volume', ''),
    ('editor', coalesce(created_by_family, '(none)')),
    ('imagery', coalesce(imagery_family, '(none)')),
    ('language', coalesce(locale_family, '(none)')),
    ('country', coalesce(country_code, '(none)')),
    ('editor_version', concat(coalesce(created_by_family, '(none)'), '\0', coalesce(created_by, '(none)')))
] AS d
WHERE created_at < toDateTime(today() - 1)
GROUP BY dimension, day, name;

-- Every distinct value of each filter field ever seen, for autocomplete
-- (queries add the last two days from the raw table, so new values show up
-- immediately).
CREATE TABLE IF NOT EXISTS filter_values
(
    field LowCardinality(String),
    value String
)
ENGINE = MergeTree
ORDER BY (field, value);

CREATE MATERIALIZED VIEW IF NOT EXISTS filter_values_refresh
REFRESH EVERY 1 DAY OFFSET 4 HOUR
TO filter_values
AS SELECT DISTINCT d.1 AS field, d.2 AS value
FROM changesets
ARRAY JOIN [
    ('contributor', user),
    ('editor', created_by_family),
    ('imagery', imagery_family),
    ('language', locale_family),
    ('country', country_code)
] AS d
WHERE d.2 IS NOT NULL;
