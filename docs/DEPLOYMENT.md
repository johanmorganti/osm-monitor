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

The dashboard is on `http://localhost:5006/`, the API / MCP docs on `http://localhost:5006/api/docs/`, the MCP server on `http://localhost:5006/mcp`.

When the site is public behind an HTTPS proxy (a Cloudflare Tunnel reaching `web` on the shared
`cloudflare` network), set `HTTPS_ONLY=true` in `.env`: http is redirected to https, with HSTS
and secure cookies (`osm_changeset_api/settings.py`). The port on 127.0.0.1 then redirects too,
so local checks need `curl -H 'X-Forwarded-Proto: https' http://127.0.0.1:5006/...`.

`deploy.sh` checks `.env` and the Docker daemon, stamps the build with the current git commit
(`GIT_VERSION`; `-dirty` when the working tree has uncommitted or untracked files, which are listed,
since the image gets them), runs `docker compose build`, the tests in the new image (`manage.py test`; none needs ClickHouse), then `docker compose up -d --wait`. It
fails, with the containers' state and logs, when web is not healthy within 3 minutes (the compose
healthcheck: a request to `/api/batch-progress/` from inside the container) or when a service
restarts or has stopped 15 s later. On fresh data directories the rest is automatic: `migrate` creates the SQLite file and its tables, and applies
the ClickHouse schema (`clickhouse_migrate`: tables, refreshable rollups, the optional Datadog
user).

On an existing ClickHouse, a skip index added by the schema (`ALTER TABLE ... ADD INDEX`) only
covers data written after it; build it for existing rows once with
`ALTER TABLE changesets MATERIALIZE INDEX <name>` (a background mutation, progress in
`system.mutations`). `0007_user_index.sql` (`idx_user_lower`) needs this.

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

**Memory.** The production host (since 2026-10-26) is a Linux server with 16 GB and 8 vCPUs,
Docker running natively (no VM). The services' `mem_limit`s in `docker-compose.yml` are sized for
it: ClickHouse 9 GB (it sizes its own memory budget from it), `web` 2 GB with 4 gunicorn workers
(`GUNICORN_WORKERS`, one per two vCPUs, 8 threads each), the diff poller 1 GB, the changeset poller
512 MB, plus a Datadog agent run outside this stack (~0.7 GB). They're ceilings: in normal use the
containers take ~2 GB, and the rest of the memory is page cache for ClickHouse's data files, which
is what makes repeated queries fast. ClickHouse's largest query in two days took 2.9 GB. There is
no swap, so keep the limits' sum under the host's memory; adjust them together for a different
machine.

Before that it ran in an 8 GB Colima VM on a 16 GB Mac (ClickHouse 5 GB, `web` 512 MB with 2
workers). Changing a Colima VM's memory needs `colima stop` then `colima start --memory <GB>` (the
other settings are kept).

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
log/trace correlation, ClickHouse Database Monitoring) is an optional overlay,
`docker-compose.datadog.yml`; without it the image has no `ddtrace` and nothing is traced. The
Datadog agent is not part of this stack: run it on the host with its own configuration (API key,
site, log collection). The overlay's header lists what it needs: APM and DogStatsD on unix
sockets in a host directory, the docker socket for autodiscovery, and the agent joining the
`datadog` network (`networks: {datadog: {name: datadog}}` in the agent's compose file) that
ClickHouse is on, which the database check connects through. To enable the overlay, set
in `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.datadog.yml
DD_CLICKHOUSE_PASSWORD=...
DD_SOCKET_DIR=/var/run/datadog   # the default; where the agent's apm.socket and dsd.socket are
```

then `./deploy.sh`, which builds the images with `ddtrace` (`DATADOG=true`,
`requirements-datadog.txt`). `clickhouse_migrate` (run by the `migrate` step) creates a
least-privilege `datadog` user from `DD_CLICKHOUSE_PASSWORD`, and the ClickHouse container's
autodiscovery label turns on the agent's ClickHouse check with Database Monitoring.

## Dependencies

`requirements.in` lists the direct dependencies, `requirements-datadog.in` adds `ddtrace`. The
image installs from `requirements.txt` / `requirements-datadog.txt`, which pin every package,
indirect ones included, with the hashes of all its files on PyPI (so builds for amd64 and arm64
both verify): pip installs nothing else (`--require-hashes`) and only wheels. After changing a
`.in` file, regenerate both locks (pip-tools; the Datadog one is constrained to the base one, so
the two images share every version):

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12.16-slim-trixie sh -c '
  pip install -q pip-tools==7.4.1 &&
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-header -o requirements.txt requirements.in &&
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-header -o requirements-datadog.txt requirements-datadog.in'
```

Pick versions released at least two weeks ago.

## Full-history import on a fresh database

The poller's first run defaults to a 365-day backfill of minutely sequences, which is redundant
once the full dump is imported. Point it at the dump's date instead:

```bash
docker compose stop poller   # deploy.sh already started it; a running poller would overwrite --reset
docker compose run --rm --no-deps web python manage.py poll_sequences --reset --backfill-days <days since the dump + 1>
docker compose up -d poller
```

Then import the dump (https://planet.openstreetmap.org/planet/changesets-latest.osm.bz2, weekly,
~9GB). A single `import_from_dump` process is bound by its own parsing (one core; ~7,500
changesets/s, ~7 h for full history), so split the file between several workers with
`--byte-range`. On the `.bz2` the range is of the compressed file: the dump is ~90,000 independent
bz2 streams, each worker decompresses only its own, and changesets spanning two ranges go to
exactly one of them, so nothing needs decompressing to disk (~70 GB unpacked):

```bash
SIZE=$(stat -c %s <dump dir>/changesets-latest.osm.bz2)   # macOS: stat -f %z
N=6
for i in $(seq 0 $((N-1))); do
  docker compose run -d --name osm-import-$i --no-deps -v <dump dir>:/dump:ro web \
    python manage.py import_from_dump /dump/changesets-latest.osm.bz2 \
      --byte-range $((SIZE*i/N)):$((SIZE*(i+1)/N)) --batch-size 2000
done
```

Measured 2026-10-26 on 8 vCPUs: 6 workers ~26,000 changesets/s together (each ~60% of a core,
waiting on its inserts), with the diff poller's backfill running alongside. A decompressed `.osm`
also works with the same flag (the range is then of the plain file).

ClickHouse's rollups rebuild from the deduplicated table on their daily refresh, so nothing needs
refreshing by hand afterwards (`SYSTEM REFRESH VIEW <view>` does it sooner).

A single process without `--byte-range` (reading the `.bz2` directly) also works, just slower. If
a worker is interrupted, re-run it with the same `--byte-range`: re-inserted rows collapse into one
(ReplacingMergeTree), or pass
`--skip <changesets already seen>` (from its progress logs) to fast-forward.
