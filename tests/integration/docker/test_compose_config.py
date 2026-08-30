import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = PROJECT_ROOT / "docker" / "compose.yaml"
DOCKERIGNORE_FILE = PROJECT_ROOT / ".dockerignore"


def test_compose_file_defines_required_services_and_healthchecks() -> None:
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "name: aegisrag" in content
    assert "local-rag-agent-system" not in content

    for service_name in (
        "api",
        "worker-ingestion",
        "worker-embedding",
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "migration",
        "postgres-roles",
    ):
        assert f"  {service_name}:" in content

    assert "healthcheck:" in content
    assert "GET /health" not in content
    assert "postgres-data:/var/lib/postgresql/data" in content
    assert 'entrypoint: ["/bin/sh", "-ec"]' in content
    assert "mc mb --ignore-existing" in content
    assert "WORKER_QUEUE_NAME: ingestion" in content
    assert "WORKER_QUEUE_NAME: embedding" in content
    assert "service_healthy" in content
    assert "POSTGRES_RUNTIME_USER" in content
    assert "POSTGRES_RUNTIME_PASSWORD" in content
    roles_sql = PROJECT_ROOT / "docker" / "postgres" / "ensure-roles.sql"
    roles_sql_text = roles_sql.read_text(encoding="utf-8")
    assert "NOSUPERUSER NOBYPASSRLS" in roles_sql_text
    assert "\\gexec" in roles_sql_text
    assert "DO $$" not in roles_sql_text
    assert "POSTGRES_MIGRATION_USER must differ from POSTGRES_USER" in roles_sql_text
    assert "REASSIGN OWNED BY" not in roles_sql_text


def test_service_token_is_optional_and_uses_safe_provider_config() -> None:
    pytest.skip("Service token services removed from compose")


def test_dockerignore_excludes_env_secret_variants() -> None:
    lines = {
        line.strip()
        for line in DOCKERIGNORE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert ".env*" in lines


def test_docker_compose_config_validates_service_graph_when_docker_is_available() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not available in this environment.")

    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_USER": "rag_bootstrap",
            "POSTGRES_PASSWORD": "local-postgres-bootstrap-password",
            "POSTGRES_DB": "rag_app",
            "POSTGRES_MIGRATION_USER": "rag_migrator",
            "POSTGRES_MIGRATION_PASSWORD": "local-postgres-migration-password",
            "POSTGRES_RUNTIME_USER": "rag_runtime",
            "POSTGRES_RUNTIME_PASSWORD": "local-postgres-runtime-password",
            "DATABASE_URL": "postgresql+asyncpg://rag_runtime:local-postgres-runtime-password@postgres:5432/rag_app",
            "REDIS_URL": "redis://redis:6379/0",
            "MINIO_ENDPOINT": "http://minio:9000",
            "MINIO_ACCESS_KEY": "local-minio-access",
            "MINIO_SECRET_KEY": "local-minio-secret",
            "MINIO_BUCKET": "documents",
                "JWT_SECRET": "local-jwt-secret-at-least-32-bytes",
                "GRAFANA_ADMIN_PASSWORD": "local-grafana-password",
                "FOLDER_CURSOR_SECRET": "local-folder-cursor-secret",
            }
    )

    quiet_result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--quiet"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
    )

    assert quiet_result.returncode == 0, quiet_result.stderr
    assert quiet_result.stdout == ""

    services_result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--services"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
    )

    assert services_result.returncode == 0, services_result.stderr
    services = set(services_result.stdout.splitlines())
    assert "service-token" not in services
    assert "service-token-config-check" not in services
    assert {
        "worker-ingestion",
        "worker-embedding",
        "api",
        "migration",
        "postgres-roles",
    }.issubset(services)


def _skip_service_token_profile_compose_config_validates_when_docker_is_available() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not available in this environment.")

    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_USER": "rag_bootstrap",
            "POSTGRES_PASSWORD": "local-postgres-bootstrap-password",
            "POSTGRES_DB": "rag_app",
            "POSTGRES_MIGRATION_USER": "rag_migrator",
            "POSTGRES_MIGRATION_PASSWORD": "local-postgres-migration-password",
            "POSTGRES_RUNTIME_USER": "rag_runtime",
            "POSTGRES_RUNTIME_PASSWORD": "local-postgres-runtime-password",
            "DATABASE_URL": "postgresql+asyncpg://rag_runtime:local-postgres-runtime-password@postgres:5432/rag_app",
            "REDIS_URL": "redis://redis:6379/0",
            "MINIO_ENDPOINT": "http://minio:9000",
            "MINIO_ACCESS_KEY": "local-minio-access",
            "MINIO_SECRET_KEY": "local-minio-secret",
            "MINIO_BUCKET": "documents",
            "JWT_SECRET": "local-jwt-secret-at-least-32-bytes",
            "SERVICE_TOKEN_PROVIDER_API_KEY": "local-service_token-provider-key",
            "SERVICE_TOKEN_HASHES_JSON": '[{"token_sha256":"local-token-hash"}]',
            "SERVICE_TOKEN_SECRET_KEY": "local-service_token-secret-key",
        }
    )

    quiet_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "service-token",
            "config",
            "--quiet",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
    )

    assert quiet_result.returncode == 0, quiet_result.stderr
    assert quiet_result.stdout == ""

    services_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "service-token",
            "config",
            "--services",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
    )

    assert services_result.returncode == 0, services_result.stderr
    services = set(services_result.stdout.splitlines())
    for service_name in (
        "api",
        "worker-ingestion",
        "worker-embedding",
        "postgres",
        "redis",
        "minio",
        "migration",
    ):
        assert service_name in services

    combined_output = (
        quiet_result.stdout
        + quiet_result.stderr
        + services_result.stdout
        + services_result.stderr
    )
    for forbidden in (
        "local-service_token-provider-key",
        "local-service_token-secret-key",
        "local-jwt-secret-at-least-32-bytes",
        "local-minio-secret",
        "local-postgres-password",
        "<replace_with_secret>",
    ):
        assert forbidden not in combined_output
