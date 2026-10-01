-- The plain ClickHouse mirror of Postgres's changesets_changeset: same columns
-- and NULL semantics, no query-specific tuning (no materialized views,
-- projections or skip indexes) until a measurement asks for one.
--
-- ReplacingMergeTree(changes_count): the writer contract is "replace a stored
-- changeset only if its changes_count grew". Rows with the same sort key are
-- deduplicated in the background, keeping the highest changes_count (the last
-- inserted on a tie, e.g. a comment-only reappearance with a newer
-- comments_count). Until a merge happens, queries that must not double count
-- an updated changeset need FINAL (or an argMax-style query).
--
-- Sort key (created_at, changeset_id): created_at first because almost every
-- query is a date range; changeset_id makes the key unique per changeset
-- (created_at never changes once a changeset exists). Monthly partitions, like
-- the Timescale hypertable's chunks.
CREATE TABLE IF NOT EXISTS changesets
(
    changeset_id              UInt64,
    created_at                DateTime('UTC'),
    closed_at                 Nullable(DateTime('UTC')),
    open                      Nullable(Bool),
    changes_count             UInt32,   -- version column: NULL stored as 0
    user                      Nullable(String),
    user_id                   Nullable(UInt32),
    min_lat                   Nullable(Float64),
    max_lat                   Nullable(Float64),
    min_lon                   Nullable(Float64),
    max_lon                   Nullable(Float64),
    comments_count            Nullable(UInt32),
    created_by                Nullable(String),
    created_by_family         LowCardinality(Nullable(String)),
    comment                   Nullable(String),
    locale                    Nullable(String),
    locale_family             LowCardinality(Nullable(String)),
    source                    Nullable(String),
    imagery_used              Array(String),
    imagery_family            LowCardinality(Nullable(String)),
    host                      Nullable(String),
    changesets_count          Nullable(UInt32),
    hashtags                  Array(String),
    streetcomplete_quest_type LowCardinality(Nullable(String)),
    review_requested          Nullable(Bool),
    remaining_tags            Nullable(String),  -- JSON object text
    geohash                   Nullable(String),
    country_code              LowCardinality(Nullable(String))
)
ENGINE = ReplacingMergeTree(changes_count)
PARTITION BY toYYYYMM(created_at)
ORDER BY (created_at, changeset_id);
