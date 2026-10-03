# Deployment

The app runs as a Docker Compose stack: `clickhouse` (analytics), `db` (Postgres with TimescaleDB +
PostGIS: app state and the deprecated Timescale copy), a one-shot `migrate`, `web` (gunicorn) and
`poller` (`poll_sequences`).

## Docker Compose

```bash
cp env.example .env    # fill in every value (deploy.sh refuses placeholders)
mkdir -p <PGDATA_DIR> <CLICKHOUSE_DATA_DIR>   # host directories named in .env
./deploy.sh            # preflight checks, build, start db, migrate, web, poller, diff-poller
```

The dashboard is on `http://localhost:5006/`, the API docs on `http://localhost:5006/api/docs/`.

`deploy.sh` checks `.env` and the Docker daemon, stamps the build with the current git commit
(`GIT_VERSION`), then runs `docker compose build && docker compose up -d`. On a fresh data
directory the rest is automatic: `db/init/` sets up `pg_stat_statements` and the app role's
defaults (see that directory's comments for applying them to an *existing* data dir), and
`migrate` creates the Postgres schema, loads `country_boundaries`, and applies the ClickHouse
schema (`clickhouse_migrate`: tables, refreshable rollups, the optional Datadog user).

Postgres and ClickHouse data live in host directories (`PGDATA_DIR`, `CLICKHOUSE_DATA_DIR`,
bind-mounted), not Docker volumes. If
Docker runs inside a VM (Colima, Docker Desktop), that directory must be shared into the VM,
otherwise the bind mount silently resolves to an empty VM-local directory. With Colima:

```bash
colima start --cpu 4 --memory 13 --disk 80 --vm-type vz --mount-type virtiofs \
  --mount "$HOME/osm-monitor-data:w" --mount "$PWD:w"
```

**Open files on a shared folder.** The process that serves a VM-shared folder (Colima's VZ
process, Docker Desktop's file sharing) keeps a host-side file descriptor open for every shared
file the VM has open or cached, for all databases together. ClickHouse stores each column of each
data part as separate files, so a large table can need well over 100,000. macOS caps a process at
`kern.maxfilesperproc` (61,440 by default); past it, Postgres and ClickHouse both fail with "Too many
open files" (ClickHouse can even fail to load a table at startup). Raise the limits and restart the
VM so its process picks them up:

```bash
sudo sh -c 'sysctl -w kern.maxfiles=524288 kern.maxfilesperproc=262144 && printf "kern.maxfiles=524288\nkern.maxfilesperproc=262144\n" >> /etc/sysctl.conf'
colima stop && colima start
```

`db`'s `mem_limit`, `shm_size` and memory settings (`shared_buffers`, `effective_cache_size`,
...) in `docker-compose.yml` are sized for ~13GB available to Docker; adjust them together for a
different machine.

**Object changes (`diff-poller`).** On its first start it follows the minutely diffs from the
latest daily diff on, then backfills 92 days of daily diffs behind that, one per round (~3 min
each, so most of a day: the first one took 13 h, the mirror's speed varies; each is a 100-180 MB
download). `object_versions` holds ~8.6 GB at 92 days (~26 bytes per object version) and its TTL
drops older days; the count tables grow ~2 MB a day and `object_edits` ~6 MB a day.
Its daily partitions add files to ClickHouse's data directory (~70 per merged part, so roughly
7,000-20,000 at 92 days), which count toward the open-files limit above. Progress is in its logs (`Daily diff written`,
`backfill_remaining`) and in `changesets_diffstate`.

**Changing `.env` recreates every service on the next `up`**, `db` included (its contents are
part of each service's config) — don't redeploy while a long import is running.

## Observability (optional)

Logs are structured JSON on stdout, so any log collector works. Datadog support (APM traces,
log/trace correlation, container logs, Postgres and ClickHouse Database Monitoring) is an optional overlay,
`docker-compose.datadog.yml`. Without it, tracing is disabled and no agent runs. To enable it,
set in `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.datadog.yml
DD_API_KEY=...
DD_SITE=datadoghq.com
DD_POSTGRES_PASSWORD=...
DD_CLICKHOUSE_PASSWORD=...
```

then `./deploy.sh`. `clickhouse_migrate` (run by the `migrate` step) creates a least-privilege `datadog` user from it,
and the ClickHouse container's autodiscovery label turns on the agent's ClickHouse check with
Database Monitoring. `DD_POSTGRES_PASSWORD` should be set before the database is first
initialized, since `db/init/01-datadog.sh` creates the `datadog` role from it. On an existing
database, run that script once by hand:
`docker compose exec db bash /docker-entrypoint-initdb.d/01-datadog.sh`.

## Full-history import on a fresh database

The poller's first run defaults to a 365-day backfill of minutely sequences, which is redundant
once the full dump is imported. Point it at the dump's date instead:

```bash
docker compose run --rm --no-deps web python manage.py poll_sequences --reset --backfill-days <days since the dump + 1>
docker compose up -d poller
```

Then import the dump (https://planet.openstreetmap.org/planet/changesets-latest.osm.bz2, weekly,
~8GB). A single `import_from_dump` process alternates between parsing and inserting, so it leaves
both itself and Postgres half idle; for full history, decompress once and split the file between
several workers with `--byte-range` (each snaps to changeset boundaries, so ranges neither overlap
nor drop anything), then refresh the continuous aggregates once at the end:

```bash
# Parallel decompression (~8x larger than the .bz2)
docker run --rm -v <dump dir>:/dump debian:bookworm-slim sh -c \
  "apt-get update && apt-get install -y lbzip2 && lbzip2 -d -k /dump/changesets-YYMMDD.osm.bz2"

# N workers over equal byte ranges of the .osm file
SIZE=$(stat -f %z <dump dir>/changesets-YYMMDD.osm)   # GNU: stat -c %s
N=4
for i in $(seq 0 $((N-1))); do
  docker compose run -d --name osm-import-$i --no-deps -v <dump dir>:/dump:ro web \
    python manage.py import_from_dump /dump/changesets-YYMMDD.osm \
      --byte-range $((SIZE*i/N)):$((SIZE*(i+1)/N)) --skip-cagg-refresh --batch-size 2000
done

# Once every worker has exited 0:
docker compose run --rm --no-deps web python manage.py refresh_caggs 2005-04-01 <dump date>
```

Pause the compression policy for the import (it would otherwise compress chunks the workers are
still writing to), then compress the backlog chunk by chunk, oldest first, and re-enable it:

```bash
docker compose exec db psql -U osm_monitor -d osm_monitor -c \
  "SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs
   WHERE proc_name = 'policy_compression' AND hypertable_name = 'changesets_changeset'"
# ... import + refresh_caggs ...
for c in $(docker compose exec -T db psql -U osm_monitor -d osm_monitor -At -c \
    "SELECT chunk_schema||'.'||chunk_name FROM timescaledb_information.chunks
     WHERE hypertable_name='changesets_changeset' AND NOT is_compressed
       AND range_end < now() - interval '30 days' ORDER BY range_start"); do
  docker compose exec -T db psql -U osm_monitor -d osm_monitor -c \
    "SET statement_timeout = 0; SELECT compress_chunk('$c', if_not_compressed => true)"
done
# then the same alter_job(...) with scheduled => true
```

A single process without `--byte-range` (reading the `.bz2` directly) also works and refreshes
the aggregates itself at the end, just slower. If a worker is interrupted, re-run it with the
same `--byte-range`: already-imported rows are skipped by the existence check, or pass
`--skip <changesets already seen>` (from its progress logs) to fast-forward.
