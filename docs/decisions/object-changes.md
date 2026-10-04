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
tell them apart. Keeping everything costs **~26 bytes per version** compressed: measured on the
full 92-day backfill (2026-07-03 to 2026-10-03), 356M versions in 8.6 GB, so ~34 GB a year if the
window grew. Merging a day fully into one part changes nothing (24.6 bytes on 2026-09-16 with 6
parts or 1). The first one-day test gave 14 bytes, but that day (2026-10-02) was mostly new nodes
with consecutive ids and few tags, which compress unusually well; on a typical day way node lists
are the biggest column (6.4 bytes per version), then ids (4.1), coordinates (3.6 each) and tags
(3.3). Codecs (Delta on `id` and `node_refs`) could likely cut that; not pursued, disk isn't the
constraint (261 GB free on 2026-10-04).

**Backfill check (2026-10-03):** all 4,778,558 closed changesets created from the first covered
day (2026-07-03) to 2026-10-01 have object counts equal to their `changes_count` (342M objects,
zero differences, checked week by week).

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

## Reading them: object_daily_rollup and the API (2026-10-03)

`timeseries` and `toplist` take `group_by`/`dimension=action|object_type|feature` with
`metric=objects` (`OBJECT_DIMENSIONS`), plus `objects_since` in the response. Objects count on
their changeset's `created_at` day, like `changes_count`, so the series add up to the existing
"objects changed"; that day and the filters come from joining `changesets` on `changeset_id`.
Per query, that join was too slow for daily series (6.9 s for 52 days by action), so
`object_daily_rollup` (`schema/0005_object_rollup.sql`) precomputes it per (dimension, day, name,
type, action, feature), refreshed daily, read up to its watermark with the raw tables after it
(`IN` on `changeset_id` narrows the raw read: 0.2-1.9 s raw against 0.02-0.17 s through the
rollup). Checked: rollup and raw paths identical on 21 cases (unfiltered, editor, country,
contributor, `(none)` imagery, ranges before coverage); totals equal `summary`'s `total_objects`
except 6 objects in 199M, from 2 changesets our `changesets` table missed the final update of
(the diffs and the OSM API agree on the real count).

**Coverage:** `object_coverage()` is the day after the earliest edit in the diffs: a changeset
created that day or later has every upload in them (they continue for up to 24 h). Queries clamp
to it and the rollup starts there. While a backfill extends coverage backward, the rollup only
covers what existed at its last refresh, so a range starting earlier reads the raw tables.

**Built once per day, never rebuilt (2026-10-04):** a closed changeset's objects don't change, nor
its day or dimensions, so `object_daily_rollup_append` (refreshable, `APPEND`, hourly) adds the
oldest missing day and leaves the rest: ~1 s for a day, whatever the count tables' size. It
replaced a daily full rebuild (`object_daily_rollup_refresh`: 9 s and 1.4 GB for 52 days, growing
with the count tables, kept forever). Recomputing a stored day gave identical rows (15,542 of
15,542). Days missing before the existing ones (coverage extended backward) are filled oldest
first; one day per refresh keeps each insert a single block, so a day is fully in or not at all.

## Edits to existing objects: object_edits and the most edited objects (2026-10-03)

`object_edits` (`schema/0006_object_edits.sql`) keeps every version after the first (modifies and
deletes; creations are ~70% of versions and left out), narrow (time, type, id, version,
changeset, contributor), **for all time**: ~3 bytes per edit, ~1.1 GB a year, so edit statistics
don't stop at `object_versions`' 92 days. `poll_diffs` writes it next to `object_versions`; the
days loaded before it existed were copied from `object_versions` (84M edits).

`/api/objects/most-edited/` ranks objects by edits over the **last 7 days** (a fixed window, the
dimension filters applied to each edit's changeset), then reads the details of the top ones from
`object_versions` (ordered by object, so a lookup). A free date range was measured and rejected:
ranking means grouping tens of millions of distinct objects, and on `object_edits` that takes
0.8 s for 7 days, 3.5 s for 30, 27 s for 67 (2.8 GB); ordering the table by object instead uses
almost no memory but is slower (4.7 s / 17 s / 32 s); the full `object_versions` table, which has
every creation too, took 19.5 s for 7 days and 4 min for 30. Every lookup by object must compare
`(type, id)` itself, not `toString(type)`, or ClickHouse loses the sort key (2.5 s -> 0.2 s).

What counts as an edit follows OSM's versioning: moving a way's nodes creates new versions of
the nodes, not of the way, so a reshaped building shows up on its corner nodes.

**Tag changes: dropped for now (2026-10-04), to revisit later.** A diff only has the new version, so a tag change is
computable only when the previous version is also in `object_versions`: on 2026-09-30 that was
19% of modifies (144K of 740K), at 21 s a day, and biased toward objects edited twice within the
window. A "most changed tags" chart would show those objects' habits, not OSM's. Exact tag changes
need every previous version: the full history planet (tier 3).

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
