"""Print optional framework constraints or a non-sensitive installed-version report.

CI installs these constraints into a disposable environment. This helper never
installs packages, contacts a provider, or changes the checked-in lock file.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def framework_requirements() -> list[Requirement]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    return [Requirement(value) for value in sorted(set(extras["langchain"] + extras["langgraph"]))]


def minimum_requirement(requirement: Requirement) -> str:
    minimums = [item.version for item in requirement.specifier if item.operator == ">="]
    if len(minimums) != 1:
        raise ValueError(f"Expected one inclusive supported minimum for {requirement.name}")
    return f"{requirement.name}=={minimums[0]}"


def version_report() -> dict[str, object]:
    versions: dict[str, str] = {}
    for requirement in framework_requirements():
        version = importlib.metadata.version(requirement.name)
        if version not in requirement.specifier:
            raise ValueError(f"Installed {requirement.name} is outside the supported range")
        versions[requirement.name] = version
    return {"framework_versions": versions, "provider_network_calls": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("minimum", "latest-compatible", "report"))
    args = parser.parse_args()
    if args.mode == "report":
        print(json.dumps(version_report(), indent=2, sort_keys=True))
        return
    for requirement in framework_requirements():
        print(minimum_requirement(requirement) if args.mode == "minimum" else str(requirement))


if __name__ == "__main__":
    main()
