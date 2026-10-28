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
if ! git diff --quiet || ! git diff --cached --quiet; then
    GIT_VERSION="${GIT_VERSION}-dirty"
fi
export GIT_VERSION

echo "Deploying version: ${GIT_VERSION}"
docker compose build
docker compose up -d
