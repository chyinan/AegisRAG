-- Idempotent database role bootstrap.  The bootstrap account is only used by
-- this one-shot service; API and workers always connect as the runtime role.
\set ON_ERROR_STOP on
\getenv migration_password POSTGRES_MIGRATION_PASSWORD
\getenv runtime_password POSTGRES_RUNTIME_PASSWORD

-- psql variables are expanded before a statement is sent to PostgreSQL.  Use
-- SELECT ... \gexec instead of referring to them inside a dollar-quoted
-- PL/pgSQL body (where the colon syntax would be invalid SQL).
SELECT format(
    'DO $a$ BEGIN IF %L = %L THEN RAISE EXCEPTION ''POSTGRES_RUNTIME_USER must differ from POSTGRES_USER''; END IF; IF %L = %L THEN RAISE EXCEPTION ''POSTGRES_MIGRATION_USER must differ from POSTGRES_RUNTIME_USER''; END IF; IF %L = %L THEN RAISE EXCEPTION ''POSTGRES_MIGRATION_USER must differ from POSTGRES_USER''; END IF; END $a$;',
    :'runtime_user', :'bootstrap_user',
    :'migration_user', :'runtime_user',
    :'migration_user', :'bootstrap_user'
) \gexec

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L',
    :'migration_user', :'migration_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user') \gexec
SELECT format(
    'ALTER ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L',
    :'migration_user', :'migration_password'
) \gexec

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L',
    :'runtime_user', :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_user') \gexec
SELECT format(
    'ALTER ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L',
    :'runtime_user', :'runtime_password'
) \gexec

-- Existing volumes may contain application objects owned by the old bootstrap
-- account. Reassign only objects in the application `public` schema; never use
-- blanket REASSIGN OWNED on a cluster bootstrap role, which can transfer
-- catalog/shared-object ownership. Future schema changes are owned by migrator.
SELECT format(
    'ALTER TABLE %I.%I OWNER TO %I', n.nspname, c.relname, :'migration_user'
)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_roles AS r ON r.oid = c.relowner
WHERE n.nspname = 'public'
  AND r.rolname = :'bootstrap_user'
  AND c.relkind IN ('r', 'p') \gexec
SELECT format(
    'ALTER SEQUENCE %I.%I OWNER TO %I', n.nspname, c.relname, :'migration_user'
)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_roles AS r ON r.oid = c.relowner
WHERE n.nspname = 'public'
  AND r.rolname = :'bootstrap_user'
  AND c.relkind = 'S' \gexec
SELECT format(
    'ALTER VIEW %I.%I OWNER TO %I', n.nspname, c.relname, :'migration_user'
)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_roles AS r ON r.oid = c.relowner
WHERE n.nspname = 'public'
  AND r.rolname = :'bootstrap_user'
  AND c.relkind = 'v' \gexec

ALTER DATABASE :"database_name" OWNER TO :"migration_user";
ALTER SCHEMA public OWNER TO :"migration_user";
GRANT CONNECT ON DATABASE :"database_name" TO :"migration_user", :"runtime_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"migration_user";
GRANT USAGE ON SCHEMA public TO :"runtime_user";

-- Keep runtime access least-privileged for both fresh and existing schemas.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"runtime_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"runtime_user";

-- pgvector is preinstalled in the image.  Creating it here lets the
-- non-superuser migration role run CREATE EXTENSION IF NOT EXISTS safely.
CREATE EXTENSION IF NOT EXISTS vector;
