-- Docker Compose initialization script for PostgreSQL roles
-- Creates the necessary roles for API and Worker services

-- Create migration role (runs alembic migrations)
CREATE ROLE rag_migrator WITH LOGIN PASSWORD '${POSTGRES_MIGRATION_PASSWORD}' CREATEDB;

-- Create runtime role (used by API and workers)
CREATE ROLE rag_runtime WITH LOGIN PASSWORD '${POSTGRES_RUNTIME_PASSWORD}';

-- Set default database for migration role
ALTER ROLE rag_migrator DATABASE = rag_app;
