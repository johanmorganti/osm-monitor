#!/bin/bash
# pg_stat_statements (query-level stats) always; Datadog Database Monitoring
# setup only when DD_POSTGRES_PASSWORD is set — Datadog is optional (see
# docker-compose.datadog.yml).
#
# pg_stat_statements also needs shared_preload_libraries and
# track_activity_query_size set on the server (the db service's `command:` in
# docker-compose.yml) — those need a restart, so they can't be set here.
#
# The DBM part creates a least-privilege `datadog` role (pg_monitor) plus a
# schema and SECURITY DEFINER function so the agent can request EXPLAIN plans
# without broader query privileges. A shell script rather than .sql because
# the role's password comes from the environment, which .sql init scripts
# don't get shell-expanded.
#
# Only runs automatically on a genuinely fresh data directory — to enable DBM
# on an existing one, run this script once by hand inside the db container.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
EOSQL

if [ -z "$DD_POSTGRES_PASSWORD" ]; then
    echo "DD_POSTGRES_PASSWORD not set — skipping Datadog DBM setup" >&2
    exit 0
fi

psql -v ON_ERROR_STOP=1 -v dd_password="$DD_POSTGRES_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datadog') THEN
            CREATE ROLE datadog WITH LOGIN;
        END IF;
    END
    $$;
    ALTER ROLE datadog WITH LOGIN PASSWORD :'dd_password';
    GRANT pg_monitor TO datadog;

    CREATE SCHEMA IF NOT EXISTS datadog;
    GRANT USAGE ON SCHEMA datadog TO datadog;
    GRANT USAGE ON SCHEMA public TO datadog;

    CREATE OR REPLACE FUNCTION datadog.explain_statement(
       l_query TEXT,
       OUT explain JSON
    )
    RETURNS SETOF JSON AS
    $fn$
    DECLARE
    curs REFCURSOR;
    plan JSON;
    BEGIN
       OPEN curs FOR EXECUTE pg_catalog.concat('EXPLAIN (FORMAT JSON) ', l_query);
       FETCH curs INTO plan;
       CLOSE curs;
       RETURN QUERY SELECT plan;
    END;
    $fn$
    LANGUAGE 'plpgsql'
    RETURNS NULL ON NULL INPUT
    SECURITY DEFINER;
EOSQL
