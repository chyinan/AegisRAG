from __future__ import annotations

from pathlib import Path


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
