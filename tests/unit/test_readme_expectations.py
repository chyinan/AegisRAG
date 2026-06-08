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
