# Statement timeout: bounded by default, opt out explicitly for long jobs

The app role (`db/init/02-role-statement-timeout.sh`) defaults to `statement_timeout = '120s'` —
inverted from the old default of unbounded-unless-told-otherwise, after an orphaned backend (its
client killed) kept running an expensive query server-side with nothing left to cancel it. `web` keeps its own tighter 30s cap via connection
`OPTIONS` (`DB_STATEMENT_TIMEOUT_MS`, `docker-compose.yml`), which wins over the role default. The
`migrate` one-shot service sets `DB_STATEMENT_TIMEOUT_MS=0` the same way, the other direction,
since migration DDL (e.g. an index build over the full hypertable) can legitimately run past 120s.
**Any new one-shot management command or long-running backfill must explicitly `SET
statement_timeout = 0` on its own connection** (see `poll_sequences.py` and every
`backfill_*`/`import_from_dump`/`refresh_rollups` command for the pattern) — it is not exempted by
default, and will otherwise be silently cancelled at 120s. `poll_sequences.py` re-issues this every
loop iteration, not just once at startup, because its exception handler calls `connection.close()`
on error and the fresh reconnect after that would otherwise silently pick the role default back up.
