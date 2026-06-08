from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("_bmad/scripts/resolve_customization.py")


def test_resolve_customization_outputs_requested_workflow_key(tmp_path: Path) -> None:
    project = tmp_path / "project"
    skill = project / ".agents" / "skills" / "bmad-code-review"
    skill.mkdir(parents=True)
    (project / "_bmad" / "custom").mkdir(parents=True)
    (skill / "customize.toml").write_text(
        """
[workflow]
on_complete = ""
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(Path.cwd() / SCRIPT),
            "--skill",
            str(skill),
            "--key",
            "workflow.on_complete",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {"workflow.on_complete": ""}


def test_resolve_customization_merges_team_and_user_overrides(tmp_path: Path) -> None:
    skill = tmp_path / "project" / ".agents" / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    custom = tmp_path / "project" / "_bmad" / "custom"
    custom.mkdir(parents=True)
    (skill / "customize.toml").write_text(
        """
[workflow]
name = "base"
activation_steps_prepend = ["base-step"]

[[workflow.menu]]
code = "A"
description = "base A"

[[workflow.menu]]
code = "B"
description = "base B"
""".strip(),
        encoding="utf-8",
    )
    (custom / "demo-skill.toml").write_text(
        """
[workflow]
name = "team"
activation_steps_prepend = ["team-step"]

[[workflow.menu]]
code = "B"
description = "team B"
""".strip(),
        encoding="utf-8",
    )
    (custom / "demo-skill.user.toml").write_text(
        """
[workflow]
on_complete = "user-hook"

[[workflow.menu]]
code = "C"
description = "user C"
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(Path.cwd() / SCRIPT), "--skill", str(skill), "--key", "workflow"],
        check=True,
        capture_output=True,
        text=True,
    )

    workflow = json.loads(result.stdout)["workflow"]
    assert workflow["name"] == "team"
    assert workflow["on_complete"] == "user-hook"
    assert workflow["activation_steps_prepend"] == ["base-step", "team-step"]
    assert workflow["menu"] == [
        {"code": "A", "description": "base A"},
        {"code": "B", "description": "team B"},
        {"code": "C", "description": "user C"},
    ]
