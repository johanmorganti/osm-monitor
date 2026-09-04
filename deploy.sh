#!/bin/sh
# Local deploy helper (no CI/CD): stamps the running containers with the
# current git commit so it shows up as the `version` tag on every Datadog
# trace/log, then rebuilds and restarts the compose services.
set -e

cd "$(dirname "$0")"

GIT_VERSION=$(git rev-parse --short HEAD)
if ! git diff --quiet || ! git diff --cached --quiet; then
    GIT_VERSION="${GIT_VERSION}-dirty"
fi
export GIT_VERSION

echo "Deploying version: ${GIT_VERSION}"
docker compose build
docker compose up -d
