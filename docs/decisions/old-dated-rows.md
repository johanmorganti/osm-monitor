# Old-dated rows in the replication stream are normal — don't "fix" them

**Read this before concluding anything about data coverage, backfill health, or the continuous
aggregates' 7-day refresh window.** It reverses two conclusions that look obvious from the data
alone and have already been reached (wrongly) once.

The replication stream at planet.osm.org publishes changesets whose *state changed*, not
changesets that were just created. Two things put a changeset in a sequence file:

1. It was created/closed recently — the overwhelming majority.
2. **Someone posted a comment on it.** Changeset discussions have no upper age limit: commenting
   requires the changeset to be *closed*, but a 2014 changeset can be commented on today and will
   reappear in today's sequence file carrying its original 2014 `created_at`.

So a handful of very old rows sitting in otherwise-empty ancient chunks is the **expected,
correct** result of live polling. It is *not* evidence of a stalled backfill, a clock bug, or a
bad import. Confirmed empirically (2026-09-17): every one of the 503 pre-2025 rows has
`comments_count > 0`, all are closed, and all carry high `id` values — i.e. they were inserted
recently by the live poller, not by the backfill. In the most recent ~74,000 inserted rows, 73,955
were 0-7 days old and 22 were over a year old (all 22 with comments); **nothing landed in
between**.

Note the mechanism is the comment, not a "comments close after 7 days" rule — there is no such
rule in OSM, and the 2014-2024 rows above disprove it. Don't write that assumption back in.

**Why `start_offset = 7 days` on all 12 CAgg refresh policies is nevertheless correct**, and must
not be widened to "cover" those old rows:

- The only aggregate-relevant field that can change after insert is `changes_count`, and that can
  only grow while the changeset is still *open* — bounded by OSM's 24h max open time / 1h idle
  timeout. 7 days is a generous margin over 24h, not an arbitrary guess.
- A comment-only reappearance of a changeset we already have writes nothing at all:
  `import_changeset_batch` (`osm_fetcher.py`) skips it unless `changes_count` actually grew.
- Widening the window would force a rescan of months of already-final buckets every 30-60 minutes,
  to correct nothing.

**The one real gap this leaves**, worth knowing but not worth widening the window for: an old
changeset we have *never seen before*, arriving because of a comment, is inserted into an ancient
chunk and will never be materialized into any CAgg — a tiny fraction of rows. More generally, any
date range the CAggs haven't materialized returns `0` rather than an error. That's accepted: in
practice it only shows up on a fresh deployment, until the post-import `refresh_caggs` has caught
up, so it isn't a reason to widen the refresh window or to add coverage reporting to the API.

**Where the 7-day window genuinely is not enough:** a deliberate backward/bulk import
(`import_from_dump`, or `poll_sequences`' backfill). Those write large volumes far outside the
window and need an explicit refresh over the imported range: `import_from_dump` does it itself at
the end, and `refresh_caggs <start> <end>` covers runs with `--skip-cagg-refresh` (parallel
`--byte-range` workers) or any other bulk write.
