from pathlib import Path


def test_readme_documents_story_6_6_tool_call_persistence_boundaries() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Epic 6.6: durable `tool_calls` persistence" in readme
    assert "independent durable `tool_calls` records" in readme
    assert "durable tool call records with safe argument/result summaries" in readme
    assert "tool_calls" in readme
    assert "Tool event streaming" in readme or "tool event streaming" in readme
    assert "final answer validation" in readme
    current_limits = readme.split("## Current Limits", maxsplit=1)[1]
    assert "durable `tool_calls` persistence" not in current_limits
