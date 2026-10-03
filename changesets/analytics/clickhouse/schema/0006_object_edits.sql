-- Edits to existing objects: every object version after the first (version
-- > 1, deletes included), narrow, kept for all time. Written by poll_diffs
-- (changesets/ingest/objects.py) next to object_versions, which keeps
-- everything but only for 92 days. See docs/decisions/object-changes.md.
--
-- ~3 bytes per edit compressed (measured: 75.6M edits, 67 days, 228 MB), so
-- ~1.1 GB a year. Most versions are creations (3.5M of 5.1M a day), which
-- this leaves out. Ordered by time: a date range reads only its rows, and
-- ranking objects over a week takes ~1 s. Over long ranges the cost is the
-- grouping itself (tens of millions of distinct objects: 27 s for 67 days).
--
-- A version's timestamp never changes, so replays land on the same key and
-- replace it.
CREATE TABLE IF NOT EXISTS object_edits
(
    timestamp    DateTime('UTC'),
    type         Enum8('node' = 1, 'way' = 2, 'relation' = 3),
    id           UInt64,
    version      UInt32,
    changeset_id UInt64,
    uid          UInt32
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, type, id, version);
