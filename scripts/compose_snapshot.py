"""Explicit offline Compose volume backup/restore; never starts applications or datastores.

This is a same-image physical snapshot for the bundled PostgreSQL, Redis and
MinIO volumes. Review docs/operations/PRODUCT_DEPLOYMENT.md before using it. External stores and
external PostgreSQL tablespaces need their own approved recovery procedures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VOLUMES = ("postgres-data", "redis-data", "minio-data")
SERVICES = ("postgres", "redis", "minio")
ROOT = Path(__file__).resolve().parents[1]


class SnapshotError(RuntimeError):
    """Only fixed safe error codes cross the command boundary."""


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode:
        raise SnapshotError("SNAPSHOT_COMMAND_FAILED")
    return result.stdout


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def compose(env_file: Path, project: str) -> list[str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", project):
        raise SnapshotError("SNAPSHOT_PROJECT_INVALID")
    if not env_file.is_file():
        raise SnapshotError("SNAPSHOT_ENV_FILE_REQUIRED")
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file.resolve()),
        "-p",
        project,
        "-f",
        str(ROOT / "docker/compose.yaml"),
    ]


def rows(output: str) -> list[dict[str, Any]]:
    if not output.strip():
        return []
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in output.splitlines() if line.strip()]
    return value if isinstance(value, list) else [value]


def configuration(command: list[str]) -> dict[str, Any]:
    # Compose expands secrets here. They remain in memory and are never reported/saved.
    config: dict[str, Any] = json.loads(run(command + ["config", "--format", "json"]))
    if not all(name in config["volumes"] for name in VOLUMES):
        raise SnapshotError("SNAPSHOT_BUNDLED_VOLUMES_REQUIRED")
    return config


def image_ids(config: dict[str, Any]) -> dict[str, str]:
    return {
        name: run(
            ["docker", "image", "inspect", config["services"][name]["image"], "--format", "{{.Id}}"]
        ).strip()
        for name in SERVICES
    }


def validate_archive(path: Path) -> None:
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            parts = Path(member.name).parts
            # The default bundled volumes have no symlinks/tablespaces. Refuse
            # links and special devices rather than following external paths.
            if (
                Path(member.name).is_absolute()
                or ".." in parts
                or not (member.isfile() or member.isdir())
            ):
                raise SnapshotError("SNAPSHOT_ARCHIVE_LAYOUT_UNSUPPORTED")


def backup(command: list[str], destination: Path, *, project: str, revision: str) -> dict[str, Any]:
    status = rows(run(command + ["ps", "--all", "--format", "json"]))
    by_service = {row["Service"]: row for row in status}
    if not all(
        name in by_service
        and by_service[name].get("State") == "exited"
        and by_service[name].get("ExitCode") == 0
        for name in SERVICES
    ) or any(row.get("State") not in {"exited", "created"} for row in status):
        raise SnapshotError("SNAPSHOT_SOURCE_MUST_BE_FULLY_STOPPED")
    config = configuration(command)
    images = image_ids(config)
    # Verify the stopped dependency containers actually used these image bytes.
    for name in SERVICES:
        actual = run(["docker", "inspect", "--format", "{{.Image}}", by_service[name]["ID"]])
        if actual.strip() != images[name]:
            raise SnapshotError("SNAPSHOT_IMAGE_IDENTITY_CHANGED")
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_project": project,
        "operator_declared_revision": revision,
        "snapshot_kind": "offline-bundled-physical-volumes",
        "images": images,
        "archives": {},
        "restore_verified": False,
    }
    for key in VOLUMES:
        target = destination / f"{key}.tar"
        with target.open("xb"):
            pass
        target.chmod(0o600)
        volume = config["volumes"][key]["name"]
        run(
            command
            + [
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "--volume",
                f"{volume}:/source:ro",
                "--volume",
                f"{destination.resolve()}:/backup",
                "backup-helper",
                "-C",
                "/source",
                "-cf",
                f"/backup/{target.name}",
                ".",
            ]
        )
        target.chmod(0o600)
        validate_archive(target)
        manifest["archives"][key] = {
            "file": target.name,
            "sha256": digest(target),
            "bytes": target.stat().st_size,
        }
    path = destination / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    return {
        "outcome": "snapshot_created",
        "restore_verified": False,
        "manifest": str(path),
        "volumes": len(VOLUMES),
    }


def restore(command: list[str], source: Path, *, project: str) -> dict[str, Any]:
    manifest = json.loads((source / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != 1
        or manifest.get("snapshot_kind") != "offline-bundled-physical-volumes"
        or project == manifest.get("source_project")
    ):
        raise SnapshotError("SNAPSHOT_MANIFEST_OR_TARGET_INVALID")
    if rows(run(command + ["ps", "--all", "--format", "json"])):
        raise SnapshotError("SNAPSHOT_TARGET_MUST_BE_NEW")
    config = configuration(command)
    if image_ids(config) != manifest["images"]:
        raise SnapshotError("SNAPSHOT_EXACT_LOCAL_IMAGES_REQUIRED")
    present = set(run(["docker", "volume", "ls", "--format", "{{.Name}}"]).splitlines())
    names = {key: config["volumes"][key]["name"] for key in VOLUMES}
    if present.intersection(names.values()):
        raise SnapshotError("SNAPSHOT_TARGET_VOLUMES_MUST_NOT_EXIST")
    for key in VOLUMES:
        entry = manifest["archives"][key]
        if entry["file"] != f"{key}.tar":
            raise SnapshotError("SNAPSHOT_ARCHIVE_NAME_INVALID")
        path = source / entry["file"]
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != entry["bytes"]
            or digest(path) != entry["sha256"]
        ):
            raise SnapshotError("SNAPSHOT_ARCHIVE_VERIFICATION_FAILED")
        validate_archive(path)
    for key in VOLUMES:
        run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                f"com.docker.compose.volume={key}",
                names[key],
            ]
        )
        run(
            command
            + [
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "--volume",
                f"{names[key]}:/target",
                "--volume",
                f"{source.resolve()}:/backup:ro",
                "backup-helper",
                "-C",
                "/target",
                "-xf",
                f"/backup/{key}.tar",
            ]
        )
    return {
        "outcome": "volumes_restored_not_started",
        "project": project,
        "restore_verified": False,
        "volumes": len(VOLUMES),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("backup", "restore"))
    result.add_argument("--env-file", required=True, type=Path)
    result.add_argument("--project", required=True, help="Source project, or fresh restore prefix")
    result.add_argument(
        "--directory",
        required=True,
        type=Path,
        help="New backup directory, or existing snapshot directory to restore",
    )
    result.add_argument("--source-revision", help="Operator-verified source Alembic revision")
    result.add_argument("--execute", action="store_true", help="Otherwise only show the plan")
    result.add_argument("--confirm-offline-source", action="store_true")
    result.add_argument("--confirm-disposable-target", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.execute:
        sys.stdout.write(
            json.dumps(
                {
                    "outcome": "plan_only",
                    "action": args.action,
                    "starts_services": False,
                    "restore_verified": False,
                }
            )
            + "\n"
        )
        return 0
    try:
        if args.action == "backup":
            if not args.confirm_offline_source or not args.source_revision:
                raise SnapshotError("SNAPSHOT_OFFLINE_SOURCE_CONFIRMATION_REQUIRED")
            command = compose(args.env_file, args.project)
            result = backup(
                command, args.directory, project=args.project, revision=args.source_revision
            )
        else:
            if not args.confirm_disposable_target:
                raise SnapshotError("SNAPSHOT_DISPOSABLE_TARGET_CONFIRMATION_REQUIRED")
            # Randomized name prevents accidental reuse of an existing project.
            project = f"{args.project[:30]}-{uuid.uuid4().hex[:12]}"
            command = compose(args.env_file, project)
            result = restore(command, args.directory, project=project)
    except SnapshotError as exc:
        result = {"outcome": "failed", "error_code": str(exc), "restore_verified": False}
    except Exception:
        # Do not print Docker output, expanded configuration, paths or raw exceptions.
        result = {
            "outcome": "failed",
            "error_code": "SNAPSHOT_OPERATION_FAILED",
            "restore_verified": False,
        }
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 1 if result["outcome"] == "failed" else 0


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
