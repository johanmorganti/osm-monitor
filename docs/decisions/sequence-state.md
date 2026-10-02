# SequenceState

A single-row model (`SequenceState`) tracks the last ingested sequence number so that
`poll_sequences` can resume after a crash without re-importing history.
