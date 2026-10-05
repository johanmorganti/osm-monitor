# Deployment

The app runs as a Docker Compose stack: `clickhouse` (every changeset and object, answers the API),
a one-shot `migrate`, `web` (gunicorn), `poller` (`poll_sequences`) and `diff-poller`
(`poll_diffs`). The app's own state (the pollers' positions, Django's tables) is a SQLite file in a
host directory shared by the last four.

## Docker Compose

```bash
cp env.example .env    # fill in every value (deploy.sh refuses placeholders)
mkdir -p <SQLITE_DIR> <CLICKHOUSE_DATA_DIR>   # host directories named in .env
./deploy.sh            # preflight checks, build, start clickhouse, migrate, web, poller, diff-poller
```

The dashboard is on `http://localhost:5006/`, the API docs on `http://localhost:5006/api/docs/`.

`deploy.sh` checks `.env` and the Docker daemon, stamps the build with the current git commit
(`GIT_VERSION`), then runs `docker compose build && docker compose up -d`. On fresh data
directories the rest is automatic: `migrate` creates the SQLite file and its tables, and applies
the ClickHouse schema (`clickhouse_migrate`: tables, refreshable rollups, the optional Datadog
user).

The SQLite file and ClickHouse's data live in host directories (`SQLITE_DIR`,
`CLICKHOUSE_DATA_DIR`, bind-mounted), not Docker volumes. If
Docker runs inside a VM (Colima, Docker Desktop), that directory must be shared into the VM,
otherwise the bind mount silently resolves to an empty VM-local directory. With Colima:

```bash
colima start --cpu 4 --memory 8 --disk 80 --vm-type vz --mount-type virtiofs \
  --mount "$HOME/osm-monitor-data:w" --mount "$PWD:w"
```

**Open files on a shared folder.** The process that serves a VM-shared folder (Colima's VZ
process, Docker Desktop's file sharing) keeps a host-side file descriptor open for every shared
file the VM has open or cached, for all databases together. It also means deleted files stay
allocated on the host until it lets go of them, i.e. until Colima restarts (seen 2026-10-05:
the 56 GB Postgres directory removed with Postgres stayed held by 12,631 handles). ClickHouse stores each column of each
data part as separate files, so a large table can need well over 100,000. macOS caps a process at
`kern.maxfilesperproc` (61,440 by default); past it, ClickHouse fails with "Too many
open files" (ClickHouse can even fail to load a table at startup). Raise the limits and restart the
VM so its process picks them up:

```bash
sudo sh -c 'sysctl -w kern.maxfiles=524288 kern.maxfilesperproc=262144 && printf "kern.maxfiles=524288\nkern.maxfilesperproc=262144\n" >> /etc/sysctl.conf'
colima stop && colima start
```

The VM has 8 GB (since 2026-10-05, was 13 GB with Postgres) on a 16 GB Mac. The services'
`mem_limit`s in `docker-compose.yml` add up to about that: ClickHouse 5 GB (it sizes its own
memory budget from it), the diff poller 1 GB, `web` and the changeset poller 512 MB each, the
optional Datadog agent 1 GB. They're ceilings: in normal use the containers take ~2 GB, and the
rest of the VM's memory is page cache for ClickHouse's data files, which is what makes repeated
queries fast. Measured before the change: 1.8 GB used, 8.8 GB of cache, ClickHouse's largest query
in two days 2.9 GB. If long-range pages get slower, 10 GB is the middle ground; adjust the limits
together for a different machine. Changing the VM's memory needs `colima stop` then
`colima start --memory <GB>` (the other settings are kept).

**Object changes (`diff-poller`).** On its first start it follows the minutely diffs from the
latest daily diff on, then backfills 92 days of daily diffs behind that, one per round (~3 min
each, so most of a day: the first one took 13 h, the mirror's speed varies; each is a 100-180 MB
download). `object_versions` holds ~8.6 GB at 92 days (~26 bytes per object version) and its TTL
drops older days; the count tables grow ~2 MB a day and `object_edits` ~6 MB a day.
Its daily partitions add files to ClickHouse's data directory (~70 per merged part, so roughly
7,000-20,000 at 92 days), which count toward the open-files limit above. Progress is in its logs (`Daily diff written`,
`backfill_remaining`) and in `changesets_diffstate`.

**A ClickHouse restart rebuilds every rollup at once.** Its refreshable views (`daily_rollup`,
the map rollups, `filter_values`) all refresh right after a restart, all full-history rebuilds
(`object_daily_rollup` only adds missing days), so for ~8 minutes the API is several times slower (measured 2026-10-03: the
most edited objects 1.3 s -> 12-28 s, objects by action 0.3 s -> 3-9 s). Deploys that don't change
ClickHouse's own config don't restart it.

**Changing `.env` recreates every service that reads it on the next `up`** (`migrate`, `web` and
both pollers; ClickHouse only when one of its own values changes) — don't redeploy while a long
import is running.

## Observability (optional)

Logs are structured JSON on stdout, so any log collector works. Datadog support (APM traces,
log/trace correlation, container logs, ClickHouse Database Monitoring) is an optional overlay,
`docker-compose.datadog.yml`. Without it, tracing is disabled and no agent runs. To enable it,
set in `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.datadog.yml
DD_API_KEY=...
DD_SITE=datadoghq.com
DD_CLICKHOUSE_PASSWORD=...
```

then `./deploy.sh`. `clickhouse_migrate` (run by the `migrate` step) creates a least-privilege `datadog` user from it,
and the ClickHouse container's autodiscovery label turns on the agent's ClickHouse check with
Database Monitoring.

## Full-history import on a fresh database

The poller's first run defaults to a 365-day backfill of minutely sequences, which is redundant
once the full dump is imported. Point it at the dump's date instead:

```bash
docker compose run --rm --no-deps web python manage.py poll_sequences --reset --backfill-days <days since the dump + 1>
docker compose up -d poller
```

Then import the dump (https://planet.openstreetmap.org/planet/changesets-latest.osm.bz2, weekly,
~8GB). A single `import_from_dump` process alternates between parsing and inserting, so it leaves
both itself and ClickHouse half idle; for full history, decompress once and split the file between
several workers with `--byte-range` (each snaps to changeset boundaries, so ranges neither overlap
nor drop anything):

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
      --byte-range $((SIZE*i/N)):$((SIZE*(i+1)/N)) --batch-size 2000
done
```

ClickHouse's rollups rebuild from the deduplicated table on their daily refresh, so nothing needs
refreshing by hand afterwards (`SYSTEM REFRESH VIEW <view>` does it sooner).

A single process without `--byte-range` (reading the `.bz2` directly) also works, just slower. If
a worker is interrupted, re-run it with the same `--byte-range`: re-inserted rows collapse into one
(ReplacingMergeTree), or pass
`--skip <changesets already seen>` (from its progress logs) to fast-forward.
