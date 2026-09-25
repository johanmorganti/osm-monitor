# Compression settings and compressing a large backlog

`changesets_changeset` has compression enabled (`0019_compress_changesets`, segmented by
`created_by_family`/editor, with a 30-day compression policy, job `policy_compression` on the
hypertable).

**Backlog pitfall:** when the policy first runs against a large uncompressed backlog (e.g. right
after a bulk import of older history), it tries to compress every eligible chunk in one pass. The
first activation of this policy did exactly that: it ran for 18+ minutes on a single chunk while
starving other queries (a poller insert stuck 12+ minutes) and was killed. For a large backlog,
unschedule the policy (`SELECT alter_job(<job_id>, scheduled => false)`), compress chunk by chunk
(`SELECT compress_chunk(c) FROM show_chunks('changesets_changeset', older_than => ...) c` in small
groups, pausing between), then re-enable it. Also unschedule it during a bulk import, so it doesn't
compress chunks that are still being written to.

**Decide `compress_segmentby` before compressing a backlog:** multiple columns are supported
(editor ~778 distinct values, imagery ~773 — both fine cardinality-wise for segment exclusion on
either filter independently), and `country_code` could join them now that it's a real stored
column (segmentby needs an actual column, not an expression — see `CLAUDE.md`'s "Geo storage: a
single geohash key" section). It's cheaper to settle the final column list once, before paying
the backlog compression cost, than to compress now and redo it later.

**Concrete cost of the current editor-only choice:** see
`docs/todo/continuous-aggregates-migration.md`'s cross-dimension gap. Any toplist/timeseries query
filtered or grouped by `imagery`/`language`/`contributor` (every dimension except editor) can't
exclude compressed segments, and times out over multi-month ranges once it hits compressed chunks.

Also open: none of the continuous aggregates has a compression policy, even though they're small
enough that compressing them is low-risk and quick.
