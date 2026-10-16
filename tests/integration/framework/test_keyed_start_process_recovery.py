"""A fresh process recovers an unknown run ID using only its original client key."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("stage", ["initial_commit", "tool_commit", "completed"])
def test_key_recovers_lost_first_response_in_fresh_process(
    tmp_path: Path, backend: str, stage: str
) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    command = [
        sys.executable,
        "-m",
        "tests.integration.framework.keyed_start_process_fixture",
        str(tmp_path),
        backend,
        stage,
    ]
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "OTEL_SDK_DISABLED": "true",
        "RAGAS_DO_NOT_TRACK": "true",
    }
    crashed = subprocess.run(
        [*command, "crash"], env=environment, capture_output=True, text=True, timeout=30
    )
    assert crashed.returncode == 42, crashed.stderr
    assert not (tmp_path / "run-id").exists()
    recovered = subprocess.run(
        [*command, "recover"], env=environment, capture_output=True, text=True, timeout=30
    )
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip() == "completed"
