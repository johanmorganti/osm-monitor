# Poller: checkpoint every N sequences instead of every one

`poll_sequences.py` currently checkpoints `SequenceState` after every sequence processed. Not
a correctness problem — re-processing a handful of sequences after a crash is idempotent
(`import_changeset_batch`'s existence check already handles it) — but it's more write I/O than
strictly needed on a disk with none to spare. Checkpointing every N sequences instead would cut
the write *count*, not just size per write (already minimized via `update_fields`).

Deliberately not done yet: it's a real behavior/recovery-granularity tradeoff (a crash mid-batch
re-does up to N-1 sequences instead of 0), not a pure win, so it deserves its own decision rather
than being bundled into an unrelated pass.
