#!/bin/sh
# Local deploy helper (no CI/CD): stamps the running containers with the
# current git commit so it shows up as the `version` tag on every Datadog
# trace/log, then rebuilds and restarts the compose services.
set -e

cd "$(dirname "$0")"

fail() { echo "deploy: $*" >&2; exit 1; }

# Preflight: catch the config gaps that otherwise surface as a half-initialized
# database or a crash-looping container, before anything is built or started.
[ -f .env ] || fail ".env missing — cp env.example .env and fill it in"
env_val() { sed -n "s/^$1=//p" .env | tail -1; }
for var in SECRET_KEY SQLITE_DIR CLICKHOUSE_PASSWORD CLICKHOUSE_DATA_DIR; do
    val=$(env_val "$var")
    [ -n "$val" ] || fail "$var is not set in .env"
    case "$val" in *change-me*|*your-secret-key-here*|*/Users/you/*) fail "$var in .env still has its env.example placeholder" ;; esac
done
# Optional Datadog layer (docker-compose.datadog.yml, enabled via COMPOSE_FILE);
# the agent runs outside this stack, with its own configuration.
case "$(env_val COMPOSE_FILE)" in *datadog*)
    [ -n "$(env_val DD_CLICKHOUSE_PASSWORD)" ] || fail "DD_CLICKHOUSE_PASSWORD must be set in .env when docker-compose.datadog.yml is enabled"
    dd_sockets=$(env_val DD_SOCKET_DIR); dd_sockets=${dd_sockets:-/var/run/datadog}
    [ -S "$dd_sockets/apm.socket" ] || echo "deploy: warning: no agent socket at $dd_sockets/apm.socket, traces will be dropped until the agent runs" >&2 ;;
esac
for var in SQLITE_DIR CLICKHOUSE_DATA_DIR; do
    dir=$(env_val "$var")
    [ -d "$dir" ] || fail "$var ($dir) does not exist — create it (and make it visible to the Docker VM, if any)"
done
docker info >/dev/null 2>&1 || fail "docker daemon not reachable"
docker compose config -q

GIT_VERSION=$(git rev-parse --short HEAD)
# Untracked files count: `COPY . .` puts them in the image like tracked ones.
if [ -n "$(git status --porcelain)" ]; then
    GIT_VERSION="${GIT_VERSION}-dirty"
    echo "deploy: working tree has uncommitted changes, the image gets them:" >&2
    git status --short >&2
fi
export GIT_VERSION

echo "Deploying version: ${GIT_VERSION}"
docker compose build
# The tests (changesets/tests/) run in the image just built, before anything is replaced; none
# needs ClickHouse or the network.
docker compose run --rm --no-deps web python manage.py test || fail "tests failed, nothing was deployed"
# --wait: returns once web is healthy and the other services run, and fails (with the
# containers' state) if one doesn't within the timeout, instead of exiting 0 on a crash loop.
docker compose up -d --wait --wait-timeout 180 || {
    docker compose ps >&2
    docker compose logs --tail 20 web >&2
    fail "the stack did not come up healthy"
}
# --wait only sees a service at the moment it checks: a poller that starts and crashes a few
# seconds later (a bad import, a settings error) passes it. Give them a moment, then require
# every long-running service to be up and not restarting.
sleep 15
unstable=$(docker compose ps -a --format '{{.Service}} {{.ID}}' | while read -r service id; do
    [ "$service" = migrate ] && continue
    # `docker compose run` leftovers (an import, a repair) are not part of the stack.
    [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.oneoff"}}' "$id")" = True ] && continue
    state=$(docker inspect -f '{{.State.Status}} restarts={{.RestartCount}}' "$id")
    [ "$state" = "running restarts=0" ] || echo "$service: $state"
done)
if [ -n "$unstable" ]; then
    echo "$unstable" >&2
    docker compose logs --tail 20 >&2
    fail "a service is not running steadily"
fi
echo "Deployed ${GIT_VERSION}: web healthy, services running"

