from __future__ import annotations

from pathlib import Path


def test_api_environment_passes_dev_auth_header_flag() -> None:
    compose = Path("docker/compose.yaml").read_text(encoding="utf-8")

    assert "ENABLE_DEV_AUTH_HEADERS: ${ENABLE_DEV_AUTH_HEADERS:-false}" in compose
