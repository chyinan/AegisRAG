from pathlib import Path


def test_readme_documents_story_6_7_final_answer_validation_boundaries() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 6.7: Agent final answer validation" in readme
    assert "durable `agent_runs` and `tool_calls` records" in readme
    assert "durable tool call records with safe argument/result summaries" in readme
    assert "agent.final_answer_validation" in readme
    assert "validated final answer" in readme
    assert "tool_calls" in readme
    assert "Tool event streaming" in readme or "tool event streaming" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "durable `tool_calls` persistence" not in current_limits
    assert "final answer validation" not in current_limits
    assert "tool event streaming" in current_limits
    assert "Open WebUI function/tool bridge" in current_limits
    assert "real LLM-backed Agent planning" in current_limits


def test_readme_documents_story_7_1_safe_source_display_boundaries() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 7.1: Safe source metadata display" in readme
    assert "source_display_name" in readme
    assert "raw `source_uri`" in readme
    assert "local paths" in readme
    assert "object keys" in readme
    assert "token-bearing URLs" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "source display" not in current_limits


def test_readme_documents_story_7_2_openwebui_auth_hardening() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 7.2: Open WebUI authentication hardening" in readme
    assert "OPENWEBUI_SERVICE_TOKEN_HASHES_JSON" in readme
    assert "Open WebUI is an entry point, not an authorization boundary" in readme
    assert "JWT bearer" in readme
    assert "service token" in readme
    assert "dev headers" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "Open WebUI authentication hardening" not in current_limits


def test_readme_documents_story_7_3_openwebui_compose_profile() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 7.3: Open WebUI Docker Compose profile" in readme
    assert "--profile open-webui" in readme
    assert "--env-file .env" in readme
    assert "OPENWEBUI_PROVIDER_API_KEY" in readme
    assert "OPENWEBUI_SECRET_KEY" in readme
    assert "OPENWEBUI_SERVICE_TOKEN_HASHES_JSON" in readme
    assert "config --quiet" in readme
    assert "open-webui-config-check" in readme
    assert "restart: unless-stopped" in readme
    assert "http://api:8000/v1" in readme
    assert "http://127.0.0.1:3000" in readme
    assert "Open WebUI is an entry point, not an authorization boundary" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "Open WebUI Docker Compose profile" not in current_limits


def test_readme_documents_story_7_4_synthetic_enterprise_walkthrough() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 7.4: Synthetic enterprise RAG walkthrough" in readme
    assert "docs/demo/enterprise-rag/manifest.json" in readme
    assert "packages.data.demo_seed validate" in readme
    assert "packages.data.demo_seed materialize" in readme
    assert "packages.data.demo_walkthrough" in readme
    assert "synthetic-only corpus" in readme
    assert "Open WebUI remains an entry point" in readme
    assert "raw `source_uri`" in readme
    assert "tests/unit/data/test_demo_seed.py" in readme
    assert "tests/integration/api/test_demo_walkthrough.py" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "synthetic demo data" not in current_limits
    assert "Source Inspector UX" not in current_limits


def test_readme_documents_story_7_6_showcase_diagnostics() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 7.6: Showcase-grade diagnostics" in readme
    assert "GET /sidecar" in readme
    assert "/sidecar/assets/sidecar.js" in readme
    assert "POST /sources/resolve" in readme
    assert "GET /documents/{document_id}/versions/{version_id}/status" in readme
    assert "POST /diagnostics/resolve" in readme
    assert "docs/demo/source-inspector-sidecar.md" in readme
    assert "sidecar is not an authorization boundary" in readme
    assert "does not save auth values" in readme
    assert "`audit:read` or" in readme
    assert "`diagnostics:read`" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "Source Inspector UX" not in current_limits
    assert "showcase-grade diagnostics" not in current_limits


def test_readme_documents_story_8_1_governance_workbench_shell() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 8.1: Governance workbench shell" in readme
    assert "GET /governance" in readme
    assert "AegisRAG Governance Workbench" in readme
    assert "Document Review" in readme
    assert "Source Evidence" in readme
    assert "Retrieval Diagnostics" in readme
    assert "Eval Evidence" in readme
    assert "Audit Explorer" in readme
    assert "Review Queue" in readme
    assert "docs/demo/governance-workbench.md" in readme
    assert "workbench is not an authorization boundary" in readme
    assert "backend AuthContext, RBAC, ACL" in readme
    assert "tests/integration/api/test_governance_routes.py" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "Governance workbench shell" not in current_limits
    assert "full review management system" in current_limits
