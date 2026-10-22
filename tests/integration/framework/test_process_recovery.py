"""A real fresh Python process must restore committed business/graph checkpoints."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_child(
    command: list[str], *, mode: str, environment: dict[str, str], directory: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*command, mode], env=environment, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired may retain bytes even when subprocess.run uses text=True.
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        artifacts = {
            name: (directory / name).exists()
            for name in ("process.db", "run-id", "dispatch-count")
        }
        pytest.fail(
            f"Child mode={mode} exceeded {exc.timeout}s; artifacts={artifacts}; "
            f"captured stderr:\n{stderr}",
            pytrace=False,
        )


@pytest.mark.parametrize("backend", ["native", "langgraph"])
def test_hard_process_exit_after_tool_commit_resumes_without_duplicate(
    tmp_path: Path, backend: str,
) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    command = [
        sys.executable, "-m", "tests.integration.framework.durable_process_fixture",
        str(tmp_path), backend,
    ]
    environment = {**os.environ, "APP_ENV": "test", "OTEL_SDK_DISABLED": "true"}
    crashed = _run_child(
        command, mode="crash", environment=environment, directory=tmp_path,
    )
    assert crashed.returncode == 42, crashed.stderr
    assert (tmp_path / "dispatch-count").read_text() == "1"
    resumed = _run_child(
        command, mode="resume", environment=environment, directory=tmp_path,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "completed"
    assert (tmp_path / "dispatch-count").read_text() == "1"
