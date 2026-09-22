# Postgres crash-restarting recurrently — one trigger fixed, root cause still open

`osm-monitor-db-1`'s Postgres has been crash-restarting recurrently for at least 3 days
(2026-09-19 through 2026-09-22 at time of writing) — confirmed via `database system was not
properly shut down; automatic recovery in progress` in its own log, 15+ times over that window,
roughly every 20 minutes to several hours apart, with no obvious daily/hourly schedule. WAL redo
on each restart has taken anywhere from 0.03s to 73s, and the container itself never restarts
(`pg_postmaster_start_time()` only changes at each of these events, not the Docker container's own
uptime) — this is Postgres's own crash-recovery, not an external kill of the container.

## What's confirmed

- **Not the Linux OOM killer**: `journalctl -k` over the same window shows zero OOM-kill events.
  Whatever is killing backends, the kernel isn't the one doing it.
- **At least one real, fixed trigger**: parallel-worker `/dev/shm` exhaustion, the same mechanism
  already documented in CLAUDE.md's "Geo storage" section (lesson 2) for `refresh_continuous_
  aggregate`/`VACUUM ANALYZE`. `cagg_maintenance.refresh_caggs_over_range` already guarded its own
  connection with `max_parallel_workers_per_gather = 0`, but every CAgg's own automatic
  `add_continuous_aggregate_policy` background refresh runs through TimescaleDB's internal
  scheduler under the same role, never through that function — so it never got the same guard.
  Fixed 2026-09-22: `db/init/03-role-parallel-workers.sh` sets this as a role-level default (plus
  a live `ALTER ROLE` for the already-existing volume), so every connection gets it, including the
  background policy workers.
- **That fix is not sufficient alone**: a crash still occurred *after* the role-level default was
  applied and verified active, during the country-CAgg backfill (see
  `docs/todo/continuous-aggregates-migration.md`'s 2026-09-22 entry) — and the crash hit
  `cagg_country_hourly`, a cheap, low-cardinality CAgg, not the expensive contributor-crossed one.
  This rules out "one specific heavy query" as the sole remaining cause.

## Working theory, unconfirmed

General host capacity pressure, not a single fixable query. This host has run with roughly
100-200MB free RAM and 750-950MB swapped in steady state for the entire 2026-09-21/22 session,
shared with several unrelated containers (wedding-bingo, vax-tracker-web, datachien-caddy,
dd-agent) — see the memory note on this project's planned migration off this host. Sustained
back-to-back DB activity for ~20-45 minutes appears able to tip this over regardless of which
specific query is running at the time, which would explain both the crash hitting a cheap CAgg and
the historical crashes that predate any of this session's work (several of the 09-20 events, and
the one during this session that happened with *no* backfill or migration running at all).

## What would actually confirm or rule this out

Not done in this session — would need dedicated investigation time, not folded into an unrelated
feature session:
- Correlate exact crash timestamps against `free`/`docker stats` history if any is retained
  (none was captured proactively this session, only spot-checks around known crash windows).
- Check whether the *other* containers on this host (wedding-bingo, vax-tracker-web,
  datachien-caddy) have their own independent memory spikes/schedules that could explain the
  crash timing better than "cumulative load from this project's own activity."
- Consider lowering `db`'s `shm_size` further headroom, or `work_mem`, as a next mitigation if the
  parallel-worker angle turns out to still be involved despite the role-level fix (a role default
  can be overridden per-session — worth double-checking nothing does that for the background
  refresh workers specifically).

## Why this wasn't chased further this session

This surfaced mid-way through adding the `country` dashboard dimension, which was already the
actual ask. The immediate risk (silent, wrong CAgg data — see the false-positive "complete" bug
in `continuous-aggregates-migration.md`) is what got fixed; the deeper "why does this host's
Postgres keep crash-restarting" question is a separate, standalone investigation, and the project
is already planning a move off this shared host (see the deployment-architecture context) which
may make it moot rather than worth solving in place.
