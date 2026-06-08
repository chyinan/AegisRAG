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
