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
    assert "WORKER_QUEUE_NAME: ingestion" in content
    assert "WORKER_QUEUE_NAME: embedding" in content
    assert "service_healthy" in content


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
        }
    )

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "worker-ingestion" in result.stdout
    assert "worker-embedding" in result.stdout
    assert "condition: service_healthy" in result.stdout
