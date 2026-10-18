"""Record which CI phases contributed to an uploaded Python coverage report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

PHASES = ("unit", "integration", "postgres", "smoke", "extended", "framework", "durable", "ragas")


def _sha(value: str | None) -> str | None:
    return value if value is not None and re.fullmatch(r"[0-9a-f]{40}", value) else None


def record(xml_path: Path, environment: Mapping[str, str]) -> dict[str, object]:
    raw = json.loads(environment.get("COVERAGE_PHASES", "{}"))
    if not isinstance(raw, dict):
        raise ValueError("COVERAGE_PHASES must be a JSON object")
    phases = {
        phase: raw.get(phase)
        if raw.get(phase) in {"success", "failure", "skipped", "cancelled"}
        else "unknown"
        for phase in PHASES
    }
    report: dict[str, object] = {
        "checkout_sha": _sha(environment.get("GITHUB_SHA")),
        "pull_request_head_sha": _sha(environment.get("PR_HEAD_SHA")),
        "pull_request_base_sha": _sha(environment.get("PR_BASE_SHA")),
        "report_present": xml_path.is_file(),
        "phases": phases,
        "all_phases_succeeded": all(value == "success" for value in phases.values()),
        "source_scope": ["packages", "apps"],
    }
    if xml_path.is_file():
        content = xml_path.read_bytes()
        root = ET.fromstring(content)
        report["xml_sha256"] = hashlib.sha256(content).hexdigest()
        report["lines_valid"] = int(root.attrib["lines-valid"])
        report["lines_covered"] = int(root.attrib["lines-covered"])
    report["report_complete"] = report["report_present"] and report["all_phases_succeeded"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--output", type=Path, default=Path("coverage-provenance.json"))
    args = parser.parse_args()
    args.output.write_text(json.dumps(record(args.xml, os.environ), indent=2) + "\n")


if __name__ == "__main__":
    main()
