# DB benchmark plan: plain Postgres (partitioned) vs TimescaleDB

> **Outcome:** superseded — the beefy machine this plan was written for wasn't available, so the
> TimescaleDB migration was done directly on the original host instead of benchmarked first.
> See `docs/ARCHITECTURE.md`'s "Why a hypertable" section for the resulting design. Kept here for
> the reasoning behind the options that were considered.

## Context

`osm-monitor` (this repo) ingests OSM changeset data into a single `changesets_changeset`
table. Today it holds ~23M rows (the past year only); the eventual goal is full 2005-present
history (~190M+ rows). Almost all real query traffic is time-filtered (see `changesets/views.py`:
`ChangesetQueryView` defaults to a 24h window, `TimeseriesView`/`SummaryView`/`ToplistView` to 7
days) — see `docs/ARCHITECTURE.md`'s "Why a hypertable" section for the full reasoning behind
what this plan was originally weighing.

**Goal of this benchmark:** decide, before doing the real migration, whether to (a) monthly
range-partition `changesets_changeset` on plain Postgres, or (b) migrate to a TimescaleDB
hypertable (which also offers continuous aggregates — a possible replacement for the hand-rolled
rollup system in `changesets/rollups.py`). Run this on a separate, beefier machine so it doesn't
compete with the actual (resource-constrained) production-adjacent host this repo normally runs
on. If run on that original host too for comparison, run the two setups **sequentially, never
simultaneously** — it's tight on memory already (see `mem_limit` entries in `docker-compose.yml`).

## Data source

A full year of already-downloaded/decompressed OSM changeset dump data exists at (on the
original host, `osm-monitor-07`):

```
/tmp/claude-1000/-home-johan-git-osm-monitor/3b185060-6d48-465e-99e7-923e603720d3/scratchpad/osm-dump/full_dump.osm
```

That's a plain (already bz2-decompressed) XML file, ~94GB, containing full OSM history —
`changesets/management/commands/import_from_dump.py` (already in this repo) streams and parses
it without loading it fully into memory, and skips the bz2 unwrap step automatically for a plain
`.osm` file. It's in a session-scratchpad path, not guaranteed to persist — copy/rsync it
somewhere durable on the beefy machine before starting. If it's gone, `changesets-latest.osm.bz2`
can be re-downloaded from `https://planet.osm.org/replication/changesets/` (see
`import_from_dump.py`'s docstring and `osm_fetcher.py` for the exact source URLs this project
already uses).

To import just "the past year" (matching what's in the current production DB), skip ahead in
the dump before importing — see `import_from_dump --help` for `--skip`/`--limit`. The exact
`--skip` value used previously was calibrated empirically (see commit history / conversation
around "Recomputed using real observed rate near that region"); recalculate it for whatever
`created_at` cutoff you want, or just import a fixed `--limit` of changesets from the end of the
file for a quick comparison rather than reproducing the exact past-year boundary.

## Setup

Two separate Postgres instances, same schema (run this repo's Django migrations against both),
same imported data:

1. **Plain Postgres, partitioned.** `postgres:16-alpine` (matches production). Since Django
   has no native `PARTITION BY` support, create the partitioned table with raw SQL (either by
   hand, or convert after `migrate` creates the normal table — either way this needs a real,
   reviewed migration plan, not just this benchmark). Monthly range partitions on `created_at`.
2. **TimescaleDB.** `timescale/timescaledb:latest-pg16` (same Postgres major version).
   `create_hypertable('changesets_changeset', 'created_at')`, sensible chunk interval (try 1
   month to start, matching the partitioning comparison). Optionally set up a continuous
   aggregate mirroring `DailyVolume`/`DailyBreakdown`'s shape to also evaluate as a rollup
   replacement, not just as a query-speed comparison.

## Queries to benchmark

Mirror what the app actually issues — see `changesets/views.py` for the real ORM code these
correspond to:

```sql
-- ChangesetQueryView: bounded list, default 24h, paginated (note the COUNT(*) DRF's
-- pagination needs — benchmark that separately, it's often the expensive part)
SELECT * FROM changesets_changeset
WHERE created_at >= :now_minus_24h AND created_at < :now
ORDER BY created_at DESC LIMIT 100;

SELECT COUNT(*) FROM changesets_changeset
WHERE created_at >= :now_minus_24h AND created_at < :now;

-- ChangesetQueryView with a filter (user/editor/imagery_family use case-insensitive
-- matching in the real app — see the UPPER() expression indexes in changesets/models.py)
SELECT * FROM changesets_changeset
WHERE created_at >= :start AND created_at < :end
  AND UPPER(created_by_family) = UPPER(:editor)
ORDER BY created_at DESC LIMIT 100;

-- TimeseriesView: daily/hourly volume over a range
SELECT created_at::date, EXTRACT(HOUR FROM created_at)::int, COUNT(*), SUM(changes_count)
FROM changesets_changeset
WHERE created_at >= :start AND created_at < :end
GROUP BY 1, 2;

-- ToplistView: top editors over a range
SELECT created_by_family, COUNT(*) FROM changesets_changeset
WHERE created_at >= :start AND created_at < :end AND created_by_family IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
```

Run each across a few different range widths (last 24h, last 7 days, last 90 days, last full
year i.e. everything) — partition pruning's advantage should grow with how narrow the range is
relative to the whole table.

## What to measure

- Query latency: wrap each in `EXPLAIN (ANALYZE, BUFFERS)`, compare planning + execution time
  and whether it's hitting an index/partition-pruned scan vs. a broader scan.
- Import time and resource usage for loading the same dataset into each.
- On-disk size (`\dt+` / `pg_total_relation_size`) — TimescaleDB's native compression on older
  chunks is one of its selling points; worth measuring separately from raw query speed.
- If testing continuous aggregates: how they compare to `refresh_rollups_incremental()` /
  `refresh_rollups_reconcile()` for both freshness and operational simplicity (less custom code
  to maintain is a real point in TimescaleDB's favor, see `changesets/rollups.py`).

## Reporting back

Summarize: latency numbers per query per range width per setup, import time, disk size, and a
recommendation. This feeds a decision on which approach (if either) to actually migrate
production to — not a decision to make unilaterally as part of this benchmark.
