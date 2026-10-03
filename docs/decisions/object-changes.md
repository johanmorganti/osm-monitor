# Object changes: per-changeset counts forever, every object version for 92 days (2026-10-03)

The changeset feed only says how many objects a changeset changed (`changes_count`). The
replication **diffs** (osmChange, `planet.osm.org/replication/minute/` and `day/`) carry every
object version uploaded, with its changeset id, its new tags and geometry. `poll_diffs` (the
`diff-poller` service) ingests them into three ClickHouse tables
(`changesets/analytics/clickhouse/schema/0004_object_changes.sql`):

- **`object_changes`**: per changeset, per file, counts by node/way/relation × create/modify/delete.
  Kept for all time. This is what other OSM statistics tools provide.
- **`object_change_features`**: the same counts split by **feature**, the object's main tag key
  from a fixed list (`changesets/ingest/osmchange.py`'s `feature_of`), plus `untagged` (mostly
  way geometry nodes), `addr`, `multipolygon`, `other`, and `unknown` for deletes. Kept for all time.
- **`object_versions`**: one row per object version with everything the diff has (tags,
  coordinates, way node lists, relation members). Kept for **92 days** (TTL, whole daily
  partitions dropped). It shows what the counts can't: objects re-edited across changesets,
  edit wars, and exact tag and geometry changes between consecutive versions in the window.

## Why both granularities

Counts per changeset are small (~2 MB a day) and answer the "what kind of edits" questions for
all time. But four "1 way modified" rows can be four unrelated edits or the same bridge edited
four times in 15 seconds (StreetComplete answers one quest per changeset); only per-object rows
tell them apart. Keeping everything costs 14 bytes per version compressed (measured on
2026-10-02: 5.08M versions, 68 MB; ~5.7 GB for 92 days, ~23 GB a year). Coordinates are the
biggest columns (they barely compress), then node lists and tags.

Disk is not what limits the window: query cost is. Grouping `object_versions` by object over
92 days (~450M rows) would take minutes and exceed ClickHouse's memory cap (6 GB), so the API
reads daily rollups built from it, never the table itself over a long range. The window can grow
once those rollups exist and their refresh time is measured.

## No before state

A diff carries only the new version: deletes have no tags, and a modify doesn't say what
changed. So a delete's feature is `unknown`, and a tag change is computable only when the
previous version is also in `object_versions`. Overpass augmented diffs (with before state) were
rejected: an external service to depend on, with its own rate limits and outages. The full
history planet (a later "tier 3" backfill) has every version.

## Files, replays and the minute/day boundary

Every row records the file it came from (`source` = minute or day, and that file's `sequence`),
and the count tables are ReplacingMergeTree keyed on it, so writing a file again replaces its
rows instead of counting them twice. `object_versions` is keyed by `(type, id, version)`, unique
in OSM, so replays and the same version from both sources collapse to one row.

**A daily diff is exactly the minutely diffs stamped within its day**: verified on 2026-10-02,
the daily file and minutely files 7310994-7312408 hold the same 4,494,005 versions, and the next
minute (stamped 00:00:07) is in none of it. So `poll_diffs` follows minutes from the first one
stamped after the latest daily diff, and backfills daily diffs from that day backward, with no
overlap. Loading the same day both ways gave identical counts for every changeset and every
type × action × feature group.

**Built-in check:** a changeset's creates + modifies + deletes, summed over its files, equal
its `changes_count` in `changesets`. Verified exactly on all 51,291 changesets opened on
2026-10-02 and closed in the window (4,492,269 objects).

## Operations

- **Downloads:** planet.osm.org redirects every file to its S3 mirror; one reused
  `requests.Session` takes a minutely file from ~2.6 s to ~0.5 s. A daily diff is ~100 MB.
- **Memory** (three out-of-memory kills found the hard way, each exit 137 in a 512 MB container):
  - The parser streams (`iterparse`) and must drop what it has read at both levels: each
    `<create>`/`<modify>`/`<delete>` block is cleared after each element, and each finished
    block is cleared from the root. Daily diffs differ in shape: 2026-10-02 had a few huge
    blocks (millions of elements each), 2026-09-16 wrapped nearly every element in its own
    block (3.4M blocks), and the empty blocks left on the root added ~250 MB.
  - The writer flushes object versions by **estimated size** (16 MB, `FLUSH_BYTES`), not by
    count: a daily diff is sorted by type, so a buffer can be all relations (~15K with ~2.7M
    members) or all ways, and the insert copies the buffer again.
  - Peak after the fixes: ~200 MB on 2026-09-16 (7.5M versions, the largest seen), ~70 MB for
    the parser alone. The container has 1 GB (raised from 512 MB on 2026-10-03).
- **Speed:** a daily diff takes 5-10 min to download (100-180 MB, the mirror's speed varies)
  and write; a minutely diff ~0.2 s to parse. The 92-day backfill takes about half a day and
  runs behind live polling, one day per round.
- **State:** `DiffState` in Postgres (live minute position, next backfill day, floor), saved
  only after a write, like `SequenceState` ([sequence-state.md](sequence-state.md)).
