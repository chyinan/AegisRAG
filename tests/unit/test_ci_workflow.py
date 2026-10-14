from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "phase",
    ["Integration mock tests", "Optional framework contracts", "Actual Ragas offline contracts"],
)
def test_ci_later_test_phases_append_to_shared_coverage(phase: str) -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    step = workflow.split(f"      - name: {phase}\n", 1)[1].split("      - name:", 1)[0]
    assert "--no-cov" not in step
    assert "--cov --cov-append --cov-report=xml" in step


def test_ci_uploads_coverage_after_every_test_phase() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    phases = [
        "Unit tests",
        "Integration mock tests",
        "RAG eval smoke gate",
        "RAG eval extended gate",
        "Install optional framework adapters",
        "Optional framework contracts",
        "Install optional Ragas evaluator",
        "Actual Ragas offline contracts",
        "Upload coverage to Codecov",
    ]
    positions = [workflow.index(f"      - name: {phase}\n") for phase in phases]
    assert positions == sorted(positions)
    assert workflow.count("uses: codecov/codecov-action@") == 1


def test_ci_workflow_runs_required_checks_and_uploads_safe_eval_artifact() -> None:
    workflow_path = Path(".github/workflows/ci.yml")
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "permissions:" in workflow
    assert "contents: read" in workflow
    assert "push:" in workflow
    assert "pull_request:" in workflow
    assert "actions/setup-python" in workflow
    assert "python-version-file: .python-version" in workflow
    assert "uv sync --dev --frozen" in workflow
    assert "uv run ruff check ." in workflow
    assert "uv run pytest tests/unit" in workflow
    assert "uv run pytest tests/integration" in workflow
    assert "uv run python -m tests.eval.rag.run_ci_smoke" in workflow
    assert "tests/eval/datasets/rag_smoke.json" in workflow
    assert "tests/eval/config/rag_smoke_gate.json" in workflow
    assert "actions/upload-artifact" in workflow
    assert "tests/eval/reports/*.json" in workflow
    assert "retention-days: 7" in workflow
    # GitHub Actions secret refs (${{ secrets.XXX }}) are fine; bare secrets aren't
    lines_with_secrets = [ln for ln in workflow.split("\n") if "secrets." in ln]
    assert any(ln.strip().startswith("token: ${{ secrets.") for ln in lines_with_secrets)
    bare = [ln for ln in lines_with_secrets if "${{" not in ln]
    assert not bare, f"Plain-text secrets: {bare}"
