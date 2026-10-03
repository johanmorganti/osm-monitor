-- Object changes from the replication diffs (poll_diffs; parsing in
-- changesets/ingest/osmchange.py, writing in changesets/ingest/objects.py).
-- See docs/decisions/object-changes.md.
--
-- Every row records the file it came from: `source` (minute or day diff) and
-- that file's `sequence`. A daily diff holds exactly the minutely diffs
-- stamped within its day, and poll_diffs never reads both for the same day,
-- so the two never overlap; replaying a file replaces its rows instead of
-- counting them twice (ReplacingMergeTree on a key including the file).
--
-- Measured on the 92-day backfill (everything kept): object_versions ~26
-- bytes per version compressed (8.6 GB for 356M versions), the two count
-- tables ~2 MB a day together.

-- Per changeset, per file: counts by type x action. Kept for all time.
-- A changeset spans several minutely files, so its counts are the sum of its
-- rows; that sum equals the changeset's changes_count (verified exactly on
-- 51,291 changesets).
CREATE TABLE IF NOT EXISTS object_changes
(
    changeset_id UInt64,
    source       Enum8('minute' = 1, 'day' = 2),
    sequence     UInt32,
    edit_time    DateTime('UTC'),   -- the latest element timestamp in this file
    node_create  UInt32,
    node_modify  UInt32,
    node_delete  UInt32,
    way_create   UInt32,
    way_modify   UInt32,
    way_delete   UInt32,
    rel_create   UInt32,
    rel_modify   UInt32,
    rel_delete   UInt32
)
ENGINE = ReplacingMergeTree
ORDER BY (changeset_id, source, sequence);

-- Per changeset, per file: counts by type x action x feature (the object's
-- main tag key, osmchange.feature_of). Kept for all time.
CREATE TABLE IF NOT EXISTS object_change_features
(
    changeset_id UInt64,
    source       Enum8('minute' = 1, 'day' = 2),
    sequence     UInt32,
    type         Enum8('node' = 1, 'way' = 2, 'relation' = 3),
    action       Enum8('create' = 1, 'modify' = 2, 'delete' = 3),
    feature      LowCardinality(String),
    count        UInt32
)
ENGINE = ReplacingMergeTree
ORDER BY (changeset_id, source, sequence, type, action, feature);

-- One row per object version, everything the diff has, for the last 92 days
-- (OBJECT_VERSIONS_DAYS in changesets/ingest/objects.py; whole daily
-- partitions are dropped). A version's timestamp never changes, so replays
-- land in the same partition and replace it. Read through daily rollups for
-- anything longer than a few days: grouping it by object over the whole
-- window would exceed the server's memory.
CREATE TABLE IF NOT EXISTS object_versions
(
    type         Enum8('node' = 1, 'way' = 2, 'relation' = 3),
    id           UInt64,
    version      UInt32,
    action       Enum8('create' = 1, 'modify' = 2, 'delete' = 3),
    changeset_id UInt64,
    timestamp    DateTime('UTC'),
    source       Enum8('minute' = 1, 'day' = 2),
    sequence     UInt32,
    uid          UInt32,
    user         LowCardinality(String),
    feature      LowCardinality(String),
    tags         Map(LowCardinality(String), String),
    lat          Nullable(Int32),   -- nodes: degrees x 1e7, OSM's own precision
    lon          Nullable(Int32),
    node_refs    Array(UInt64),     -- ways
    members      Array(Tuple(type Enum8('node' = 1, 'way' = 2, 'relation' = 3), ref UInt64, role LowCardinality(String)))
)
ENGINE = ReplacingMergeTree
PARTITION BY toDate(timestamp)
ORDER BY (type, id, version)
TTL timestamp + INTERVAL 92 DAY
SETTINGS ttl_only_drop_parts = 1;
