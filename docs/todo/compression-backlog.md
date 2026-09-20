# Compression backlog not yet compressed — policy manually paused

`changesets_changeset` has compression enabled (`0019_compress_changesets`, segmented by
`created_by_family`/editor, 30-day policy) but the policy job (job_id 1000) is currently
**unscheduled** (`SELECT alter_job(1000, scheduled => false);`) and only one chunk is actually
compressed. First activation tried to compress the entire ~13-month backlog (12 real chunks,
~11GB) in one call — the same "processes the whole backlog in one pass" trap as the rollup
incremental-refresh incident — and was killed after 18+ minutes to compress a single 84MB chunk
while actively starving other queries (a poller rollup insert stuck 12+ minutes). Deliberately
left paused rather than worked through gradually; re-enabling needs either a controlled
one-chunk-at-a-time manual pass or accepting a similar I/O spike.

Before doing that backlog pass, reconsider `compress_segmentby`: multiple columns are supported
(editor ~778 distinct, imagery ~773 distinct — both fine cardinality-wise for segment-exclusion
benefit on either filter independently), and a future `country` filter
could join it now that it's a real stored column (`Changeset.country_code`, segmentby requires an
actual column, not an expression — see `CLAUDE.md`'s "Geo storage: a single geohash key" section)
— cheaper to decide
the final column list once, before paying the backlog compression cost, than to compress now and
redo it later.

**Confirmed concrete cost of the current editor-only choice**: see
`docs/todo/continuous-aggregates-migration.md`'s cross-dimension gap — any toplist/timeseries
query filtered or grouped by `imagery`/`language`/`contributor` (i.e. every dimension except
editor) can't exclude compressed segments and times out over multi-month ranges once real data
hits those chunks.

Also unclaimed: none of the 12 continuous aggregates has a compression policy either, despite
being small enough (2.1 GB total) that compressing them is low-risk and quick — see the data
architecture teardown (2026-09-17/18) for sizes per CAgg.
