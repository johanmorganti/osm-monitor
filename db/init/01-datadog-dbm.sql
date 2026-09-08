-- One-time setup for Datadog Database Monitoring (DBM). Only runs
-- automatically on a FRESH Postgres data directory (postgres:16-alpine's
-- entrypoint executes everything under /docker-entrypoint-initdb.d/ only
-- during first-time initialization) — on an existing volume this needs
-- to be applied manually once, the same SQL run directly.
--
-- Also requires shared_preload_libraries=pg_stat_statements and
-- track_activity_query_size set high enough to avoid truncated query text
-- in DBM samples — see the db service's `command:` in docker-compose.yml,
-- both need a Postgres restart to take effect (can't be set here).

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Assumes the `datadog` role already exists with pg_monitor granted —
-- that role isn't created by an init script (needs DD_POSTGRES_PASSWORD,
-- which .sql init scripts don't get shell-expanded), it's a manual
-- one-time step: CREATE ROLE datadog WITH LOGIN PASSWORD '...'; GRANT
-- pg_monitor TO datadog; — do that first on a genuinely fresh setup.
--
-- The role needs this schema + SECURITY DEFINER function so it can
-- request EXPLAIN plans for DBM's execution-plan collection without
-- needing broader query privileges.
CREATE SCHEMA IF NOT EXISTS datadog;
GRANT USAGE ON SCHEMA datadog TO datadog;
GRANT USAGE ON SCHEMA public TO datadog;

CREATE OR REPLACE FUNCTION datadog.explain_statement(
   l_query TEXT,
   OUT explain JSON
)
RETURNS SETOF JSON AS
$$
DECLARE
curs REFCURSOR;
plan JSON;

BEGIN
   OPEN curs FOR EXECUTE pg_catalog.concat('EXPLAIN (FORMAT JSON) ', l_query);
   FETCH curs INTO plan;
   CLOSE curs;
   RETURN QUERY SELECT plan;
END;
$$
LANGUAGE 'plpgsql'
RETURNS NULL ON NULL INPUT
SECURITY DEFINER;
