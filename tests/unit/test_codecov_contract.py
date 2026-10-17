"""Keep coverage reporting strict and transparent, independently of upload success."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("kind,target", [("project", "auto"), ("patch", 100)])
def test_codecov_requires_nonregression_and_complete_patch(kind: str, target: str | int) -> None:
    config = yaml.safe_load((ROOT / "codecov.yml").read_text(encoding="utf-8"))
    status = config["coverage"]["status"][kind]["default"]
    assert status["target"] == target
    assert status["threshold"] == 0
    assert status["informational"] is False
    assert status["if_not_found"] == "failure"
    assert "paths" not in status
    assert "flags" not in status
    assert "ignore" not in config
    assert "flags" not in config
    assert "fixes" not in config


def test_codecov_comment_exposes_project_and_patch_without_waiting_for_duplicate_uploads() -> None:
    config = yaml.safe_load((ROOT / "codecov.yml").read_text(encoding="utf-8"))
    comment = config["comment"]
    assert comment["hide_project_coverage"] is False
    assert comment["require_head"] is True
    assert {"header", "diff", "files", "footer"} <= {
        section.strip() for section in comment["layout"].split(",")
    }
    # Main has one upload; PRs can also have a duplicate push-event upload.
    assert comment.get("after_n_builds", 1) == 1
