from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "phase",
    [
        "Integration mock tests", "PostgreSQL pilot gate",
        "Optional framework contracts", "Actual Ragas offline contracts",
    ],
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
        "PostgreSQL pilot gate",
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


def test_image_publication_waits_for_backend_and_web_gates() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    publisher = workflow.split("  build-and-push:\n", 1)[1]
    assert "    needs: [test, web]\n" in publisher
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in publisher
    assert "always()" not in publisher
    assert "${{ github.sha }}" in publisher


def test_browser_gate_is_required_in_web_job_after_production_build() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    web = workflow.split("  web:\n", 1)[1].split("  build-and-push:\n", 1)[0]
    assert web.index("npm run build") < web.index("npm run test:e2e")
    assert 'PLAYWRIGHT_USE_BUILD: "1"' in web
    assert "npx playwright install --with-deps chromium" in web
    assert "continue-on-error" not in web
    assert "apps/web/test-results/" in web
    assert "apps/web/playwright-report/" in web


def test_postgres_gate_runs_real_fresh_migrations_with_separate_roles() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    backend = workflow.split("  test:\n", 1)[1].split("  web:\n", 1)[0]
    assert "image: pgvector/pgvector:pg17" in backend
    assert "docker/postgres/ensure-roles.sql" in backend
    assert "${{ job.services.postgres.id }}" in backend
    gate = backend.split("      - name: PostgreSQL pilot gate\n", 1)[1].split(
        "      - name:", 1
    )[0]
    assert 'AEGISRAG_PILOT_DISPOSABLE_DATABASE: "1"' in gate
    assert "postgresql+asyncpg://rag_migrator:" in gate
    assert "postgresql+asyncpg://rag_runtime:" in gate
    assert "tests/integration/storage/test_postgres_pilot.py" in gate
    assert "tests/integration/storage/test_postgres_rls_runtime.py" in gate
    assert "continue-on-error" not in gate
    # The fixture, not a pre-step, must own fresh migration execution.
    assert "alembic upgrade" not in backend


def test_postgres_gate_also_requires_real_redis_handoff_probe() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "image: redis:8.0.2-alpine" in workflow
    step = workflow.split("      - name: PostgreSQL pilot gate\n", 1)[1].split(
        "      - name:", 1,
    )[0]
    assert "AEGISRAG_PILOT_REDIS_URL: redis://127.0.0.1:6379/0" in step
    assert "tests/integration/storage/test_postgres_pilot.py" in step
    pilot = Path("tests/integration/storage/test_postgres_pilot.py").read_text(encoding="utf-8")
    assert '"before_accept", "after_accept"' in pilot
    assert "test_postgres_redis_committed_handoff_reaches_ready_once" in pilot
