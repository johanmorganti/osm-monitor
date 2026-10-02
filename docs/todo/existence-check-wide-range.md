# Existence check spans every chunk between a batch's oldest and newest changeset

`TimescaleWriter.write` (`changesets/ingest/writers/timescale.py`) looks up which changesets of a
batch are already stored with one query bounded by the batch's `min(created_at)`..`max(created_at)`.
For a normal replication sequence that's a window of minutes, so chunk exclusion keeps it cheap. But
a batch can also carry an old changeset that resurfaced because someone commented on it (see
`docs/decisions/old-dated-rows.md`), and then the range covers every monthly chunk in between, most of
them compressed.

Measured 2026-10-01: importing 5 changesets spanning 2006–2020 took **~120 s**, almost all of it
this lookup. The live poller hits the same shape whenever a sequence includes an old commented
changeset.

Fix: the same per-day grouping the writer's DELETE already uses (one bounded query per distinct
`created_at` day in the batch), so each lookup only touches its own chunk.
