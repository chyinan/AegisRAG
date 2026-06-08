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
        "migration",
    ):
        assert f"  {service_name}:" in content

    assert "healthcheck:" in content
    assert "GET /health" not in content
    assert "postgres-data:/var/lib/postgresql" in content
    assert "postgres-data:/var/lib/postgresql/data" not in content
    assert "WORKER_QUEUE_NAME: ingestion" in content
    assert "WORKER_QUEUE_NAME: embedding" in content
    assert "service_healthy" in content


def test_open_webui_service_is_optional_and_uses_safe_provider_config() -> None:
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "  open-webui:" in content
    assert 'profiles: ["open-webui"]' in content
    assert "image: ${OPENWEBUI_IMAGE:-ghcr.io/open-webui/open-webui:main}" in content
    assert '"${OPENWEBUI_PORT:-3000}:8080"' in content
    assert "open-webui-data:/app/backend/data" in content
    assert "OPENAI_API_BASE_URL: ${OPENWEBUI_OPENAI_API_BASE_URL:-http://api:8000/v1}" in content
    assert "OPENAI_API_KEY: ${OPENWEBUI_PROVIDER_API_KEY:-}" in content
    assert "WEBUI_SECRET_KEY: ${OPENWEBUI_SECRET_KEY:-}" in content
    assert "  open-webui-config-check:" in content
    assert "Open WebUI provider key hash is missing" in content
    assert "api:" in content
    assert "condition: service_healthy" in content
    assert "condition: service_completed_successfully" in content
    assert "open-webui-data:" in content

    api_section = content.split("  api:", maxsplit=1)[1].split("  worker-ingestion:", maxsplit=1)[0]
    assert "open-webui" not in api_section

    open_webui_section = content.split("  open-webui:", maxsplit=1)[1].split(
        "volumes:",
        maxsplit=1,
    )[0]
    for secret_name in (
        "JWT_SECRET",
        "POSTGRES_PASSWORD",
        "MINIO_SECRET_KEY",
        "OPENWEBUI_SERVICE_TOKEN_HASHES_JSON",
    ):
        assert secret_name not in open_webui_section


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
            "POSTGRES_USER": "rag_app",
            "POSTGRES_PASSWORD": "local-postgres-password",
            "POSTGRES_DB": "rag_app",
            "DATABASE_URL": "postgresql+asyncpg://rag_app:local-postgres-password@postgres:5432/rag_app",
            "REDIS_URL": "redis://redis:6379/0",
            "MINIO_ENDPOINT": "http://minio:9000",
            "MINIO_ACCESS_KEY": "local-minio-access",
            "MINIO_SECRET_KEY": "local-minio-secret",
            "MINIO_BUCKET": "documents",
            "JWT_SECRET": "local-jwt-secret-at-least-32-bytes",
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
    assert "open-webui" not in services
    assert "open-webui-config-check" not in services
    assert {"worker-ingestion", "worker-embedding", "api", "migration"}.issubset(services)


def test_open_webui_profile_compose_config_validates_when_docker_is_available() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not available in this environment.")

    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_USER": "rag_app",
            "POSTGRES_PASSWORD": "local-postgres-password",
            "POSTGRES_DB": "rag_app",
            "DATABASE_URL": "postgresql+asyncpg://rag_app:local-postgres-password@postgres:5432/rag_app",
            "REDIS_URL": "redis://redis:6379/0",
            "MINIO_ENDPOINT": "http://minio:9000",
            "MINIO_ACCESS_KEY": "local-minio-access",
            "MINIO_SECRET_KEY": "local-minio-secret",
            "MINIO_BUCKET": "documents",
            "JWT_SECRET": "local-jwt-secret-at-least-32-bytes",
            "OPENWEBUI_PROVIDER_API_KEY": "local-openwebui-provider-key",
            "OPENWEBUI_SERVICE_TOKEN_HASHES_JSON": '[{"token_sha256":"local-token-hash"}]',
            "OPENWEBUI_SECRET_KEY": "local-openwebui-secret-key",
        }
    )

    quiet_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "open-webui",
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
            "open-webui",
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
        "open-webui-config-check",
        "open-webui",
    ):
        assert service_name in services

    combined_output = (
        quiet_result.stdout
        + quiet_result.stderr
        + services_result.stdout
        + services_result.stderr
    )
    for forbidden in (
        "local-openwebui-provider-key",
        "local-openwebui-secret-key",
        "local-jwt-secret-at-least-32-bytes",
        "local-minio-secret",
        "local-postgres-password",
        "<replace_with_secret>",
        "<replace_with_local_openwebui_provider_key>",
    ):
        assert forbidden not in combined_output
