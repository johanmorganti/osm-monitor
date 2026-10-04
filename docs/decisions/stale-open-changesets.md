# Changesets the feed leaves open are re-fetched from the OSM API (2026-10-04)

OSM's changeset replication feed sometimes never publishes a changeset's closing update. Found
2026-10-03 while checking the object counts from the diffs against `changes_count`: 61,116
changesets created since September (~1,600 a day, ~3.5%) were stored as open, with no
`closed_at`, in both ClickHouse and Postgres. For two of them (189345666, 189623541, both
StreetComplete), every feed file from creation to two hours after closing was scanned: each
appears exactly once, right after creation (open, 1 change), and never again, while the OSM API
has them closed with 5 and 3 changes. Our poller stored what the feed gave it. Most of the stale
ones still have the right count (the diffs disagreed with only 3 of ~59K, 15 objects in all); what
they lack is `closed_at` / `open`.

**Fix:** OSM closes every changeset within 24 h, so one still stored as open 25 h after creation is
stale. `changesets/ingest/reconcile.py` finds those (ClickHouse, the primary store) and
re-fetches them from the API (`/api/0.6/changesets?changesets=…`, 100 per request, 1 s apart),
then writes them through `import_changeset_batch` like any replication record (location, every
writer). `poll_sequences` does it hourly for the last 3 days of creations (`--reconcile-interval`,
~16 requests a day); `manage.py reconcile_open_changesets --days N` covers a wider range (the
61,116 were fixed with `--days 60`). The API names the count `changes_count` (the feed and the
dump say `num_changes`) and adds `created_count` / `modified_count` / `deleted_count`, which the
parser skips.

**Writers:** the contract was "replace a stored changeset only if its `changes_count` grew", so a
closing record with the same count was dropped by the Timescale writer (ClickHouse keeps the last
row inserted on a tie, so it took it). It's now "grew, or closed with the same count" (the stored
copy open, the incoming one closed), in both writers' docstrings and `writers/base.py`.

**Result (2026-10-04):** the 61,116 fetched, none still open; afterwards every changeset with
object data (4,884,036 since 2026-07-03) has object counts equal to its `changes_count`, the 3 that
differed included. Postgres had a second, larger set: 35,161 changesets the feed *had* closed
(ClickHouse had them closed) but the old Timescale writer skipped, the closing record having the
same count. The hourly check reads ClickHouse, so they were re-fetched once by id from Postgres
(every changeset created before the writer fix and still open there); since the fix, closing
records reach Postgres too. The API answered 503 after ~170 requests in a row: `reconcile` waits
and retries (30 / 60 / 120 s) on 429 and 5xx.

Not changed: TimescaleDB's continuous aggregates only refresh the last 7 days
([old-dated-rows.md](old-dated-rows.md)), so a corrected count older than that stays stale there
(TimescaleDB is deprecated; ClickHouse's rollups are rebuilt from deduplicated data daily).
