# Set by BuildKit to the build's architecture (amd64, arm64); picks the tailwind-* stage below.
ARG TARGETARCH

# Pinned to a digest so a rebuild gets the same OS packages; bump it (tag and
# digest together) to pick up Debian security fixes: docker pull the tag, then
# docker image inspect --format '{{index .RepoDigests 0}}'.
FROM python:3.12.16-slim-trixie@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07 AS base

# Tailwind CSS for every page, compiled here from the templates and static/js so it always
# matches them (docs/decisions/self-hosted-assets.md). The standalone CLI is pinned by checksum
# (from the release's sha256sums.txt); only the stage for the build's architecture runs.
FROM base AS tailwind-amd64
ADD --checksum=sha256:7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4 --chmod=755 \
    https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 /usr/local/bin/tailwindcss
FROM base AS tailwind-arm64
ADD --checksum=sha256:69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b --chmod=755 \
    https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-arm64 /usr/local/bin/tailwindcss

FROM tailwind-${TARGETARCH} AS css
WORKDIR /src
COPY tailwind/ tailwind/
COPY changesets/templates/ changesets/templates/
COPY static/js/ static/js/
RUN tailwindcss -c tailwind/tailwind.config.js -i tailwind/input.css -o /site.css --minify

FROM base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# DATADOG=true (set by docker-compose.datadog.yml) adds ddtrace for APM tracing. Both files
# lock every package, indirect ones included, to a version and its hashes: pip refuses anything
# else (--require-hashes), and wheels only (no package builds code at install time).
ARG DATADOG=false
COPY requirements.txt requirements-datadog.txt ./
RUN pip install --no-cache-dir --require-hashes --only-binary :all: --retries 10 --timeout 60 \
    -r "$([ "$DATADOG" = true ] && echo requirements-datadog.txt || echo requirements.txt)"

COPY . .
COPY --from=css /site.css static/css/site.css

# Static files are collected here, once, so a manifest error fails the build instead of every
# container at start. The settings only need these two to import; nothing is read from the .env.
RUN SECRET_KEY=build ALLOWED_HOSTS=build python manage.py collectstatic --no-input

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
