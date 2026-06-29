from pathlib import Path

README_PATH = Path("README.md")


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _section(markdown: str, heading: str) -> str:
    marker = f"## {heading}"
    _, section = markdown.split(marker, maxsplit=1)
    next_heading = section.find("\n## ")
    if next_heading == -1:
        return section.strip()
    return section[:next_heading].strip()


def test_readme_build_status_stays_concise() -> None:
    build_status = _section(_readme(), "Build Status")
    prose_before_roadmap = build_status.split("```mermaid", maxsplit=1)[0]

    assert len(prose_before_roadmap.split()) <= 220
    assert prose_before_roadmap.count("Story ") == 0


def test_readme_keeps_core_production_sections() -> None:
    readme = _readme()

    for heading in (
        "Tech Stack",
        "Architecture",
        "Benchmarks",
        "Quickstart",
        "Highlights",
        "Documentation",
        "Build Status",
        "Why AegisRAG",
        "Project Structure",
        "Evaluation and Tests",
        "Authentication",
        "Current Limits",
        "Contributing",
    ):
        assert f"## {heading}" in readme
