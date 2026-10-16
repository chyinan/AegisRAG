"""Positive offline deployment contracts; no Docker, Helm, Redis or provider calls."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from apps.worker.queue_metrics import collect_metrics
from packages.operations import upgrade_check as check
from scripts import compose_snapshot
from tests.unit.operations.test_upgrade_readiness import policies, tables, vector_schema

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "revision,count", list(zip(check.SUPPORTED_REVISIONS, (18, 18, 21, 22, 24, 25), strict=True))
)
def test_supported_historical_schema_contract(revision: str, count: int) -> None:
    names = check.tenant_tables_for_revision(revision)
    expected_tables = [row for row in tables() if row["relname"] in names]
    expected_policies = [row for row in policies() if row["relname"] in names]
    result = check.check_rls(expected_tables, expected_policies, revision)
    assert result.status == check.Status.PASS
    assert result.facts["tables_checked"] == count
    assert check.check_revision([revision], check.load_migration_plan()).status == check.Status.PASS
    schema = vector_schema(
        check.RLS_REVISION if revision == check.RLS_REVISION else check.VECTOR_REVISION
    )
    assert check.check_vector_schema([schema], revision).status == check.Status.PASS


def test_reviewed_migration_head_and_chain() -> None:
    plan = check.load_migration_plan()
    assert plan.head == "20261016_0021"
    assert len(plan.revisions) == 21
    assert compose_snapshot.VOLUMES == ("postgres-data", "redis-data", "minio-data")


def test_compose_opt_in_workers_and_internal_metrics() -> None:
    services = yaml.safe_load((ROOT / "docker/compose.yaml").read_text())["services"]
    assert services["worker-folder-sync"]["profiles"] == ["folder-sync"]
    assert services["worker-folder-sync"]["network_mode"] == "service:api"
    assert services["worker-durable"]["profiles"] == ["durable-agent"]
    assert "--factory" in services["worker-durable"]["command"]
    assert "ports" not in services["queue-metrics"]
    for name in ("worker-ingestion", "worker-embedding", "worker-durable"):
        assert "960s" in services[name]["stop_grace_period"]
    assert services["backup-helper"]["network_mode"] == "none"
    assert services["backup-helper"]["entrypoint"] == ["tar"]


def test_chart_defaults_preserve_external_and_offline_boundaries() -> None:
    values = yaml.safe_load((ROOT / "helm/aegisrag/values.yaml").read_text())
    for name in ("postgres", "redis", "minio"):
        assert values[name]["enabled"] is False
    assert values["api"]["env"]["LLM_PROVIDER"] == "fake"
    assert values["api"]["env"]["EMBEDDING_PROVIDER"] == "fake"
    assert values["api"]["env"]["AGENT_DURABLE_ENABLED"] == "false"
    assert values["worker"]["folderSync"]["enabled"] is False
    assert values["worker"]["durable"]["enabled"] is False
    assert values["migration"]["expectedRevision"] == check.TARGET_REVISION
    bundled_sql = ROOT / "helm/aegisrag/files/ensure-roles.sql"
    assert bundled_sql.read_bytes() == (ROOT / "docker/postgres/ensure-roles.sql").read_bytes()
    assert json.loads((ROOT / "helm/aegisrag/files/aegisrag-api.json").read_text())["panels"]


def test_queue_exporter_batches_read_only_cardinalities() -> None:
    pipeline = Mock()
    pipeline.__enter__ = Mock(return_value=pipeline)
    pipeline.__exit__ = Mock(return_value=None)
    pipeline.execute.return_value = [1, 5, 0, 1, 2, 0, 0, 1, 3, 0, 0, 0, 0, 1]
    connection = Mock()
    connection.pipeline.return_value = pipeline
    result = collect_metrics(connection, ("ingestion", "embedding"))
    connection.pipeline.assert_called_once_with(transaction=False)
    assert 'aegisrag_queue_entries{queue_slot="0",state="queued"} 5' in result
    assert 'aegisrag_queue_entries{queue_slot="1",state="failed"} 1' in result
    assert "aegisrag_queue_exporter_up 1" in result
    assert {call[0] for call in pipeline.method_calls} == {"sismember", "llen", "zcard", "execute"}
    assert "ingestion" not in result


def test_snapshot_cli_plan_does_not_call_docker(monkeypatch, capsys) -> None:
    monkeypatch.setattr(compose_snapshot, "run", lambda command: pytest.fail("plan is read-only"))
    result = compose_snapshot.main(
        [
            "backup",
            "--env-file",
            ".env",
            "--project",
            "aegis",
            "--directory",
            "/tmp/unused-snapshot",
        ]
    )
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "outcome": "plan_only",
        "action": "backup",
        "starts_services": False,
        "restore_verified": False,
    }


def archive(path: Path) -> None:
    with tarfile.open(path, "w") as value:
        payload = b"synthetic volume bytes"
        member = tarfile.TarInfo("./synthetic.dat")
        member.size = len(payload)
        value.addfile(member, io.BytesIO(payload))


def config(project: str) -> dict:
    return {
        "volumes": {key: {"name": f"{project}_{key}"} for key in compose_snapshot.VOLUMES},
        "services": {key: {"image": f"synthetic-{key}:test"} for key in compose_snapshot.SERVICES},
    }


def test_offline_backup_and_separate_restore_command_contract(monkeypatch, tmp_path) -> None:
    target = tmp_path / "snapshot"
    commands = []
    restored = False

    def run(command: list[str]) -> str:
        nonlocal restored
        commands.append(command)
        if command[-4:] == ["ps", "--all", "--format", "json"]:
            if restored:
                return "[]"
            return json.dumps(
                [
                    {"Service": name, "State": "exited", "ExitCode": 0, "ID": name}
                    for name in compose_snapshot.SERVICES
                ]
            )
        if command[-3:] == ["config", "--format", "json"]:
            return json.dumps(config("restore" if restored else "source"))
        if command[:3] == ["docker", "image", "inspect"]:
            return "sha256:synthetic"
        if command[:2] == ["docker", "inspect"]:
            return "sha256:synthetic"
        if command[:3] == ["docker", "volume", "ls"]:
            return "source_postgres-data\nsource_redis-data\nsource_minio-data\n"
        if "-cf" in command:
            archive(target / Path(command[-2]).name)
        return ""

    monkeypatch.setattr(compose_snapshot, "run", run)
    report = compose_snapshot.backup(
        ["docker", "compose"], target, project="source", revision=check.TARGET_REVISION
    )
    assert report["outcome"] == "snapshot_created"
    assert report["restore_verified"] is False
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["operator_declared_revision"] == check.TARGET_REVISION
    assert len(manifest["archives"]) == 3
    restored = True
    report = compose_snapshot.restore(["docker", "compose"], target, project="restore")
    assert report["outcome"] == "volumes_restored_not_started"
    assert report["restore_verified"] is False
    assert sum("-cf" in command for command in commands) == 3
    assert sum("-xf" in command for command in commands) == 3
    assert not any("up" in command or "start" in command for command in commands)


def test_feature_settings_reach_compose_and_helm_without_enabling_them() -> None:
    services = yaml.safe_load((ROOT / "docker/compose.yaml").read_text())["services"]
    helm = yaml.safe_load((ROOT / "helm/aegisrag/values.yaml").read_text())
    for name in (
        "AGENT_DURABLE_BACKGROUND_ENABLED",
        "SUPPORT_TICKET_ENABLED",
        "FOLDER_SYNC_ENABLED",
        "COST_ACCOUNTING_ENABLED",
    ):
        assert services["api"]["environment"][name] == f"${{{name}:-false}}"
        assert helm["api"]["env"][name] == "false"
    for name in (
        "FOLDER_SYNC_BATCH_TIMEOUT_SECONDS",
        "FOLDER_SYNC_MAX_MANAGED_SOURCES",
        "COST_MODEL_PRICES_JSON",
        "COST_CURRENCY",
        "COST_IO_TIMEOUT_SECONDS",
    ):
        assert name in services["api"]["environment"]
        assert name in helm["api"]["env"]
    assert json.loads(helm["api"]["env"]["COST_MODEL_PRICES_JSON"]) == {"prices": []}
    for name in ("COST_MONTHLY_BUDGET", "COST_MINIMUM_RESERVATION"):
        assert services["api"]["environment"][name] == f"${{{name}:-}}"
    assert "FOLDER_SYNC_BEARER_TOKEN" not in services["api"]["environment"]
    assert "fsGroup: 65534" in (ROOT / "helm/aegisrag/templates/monitoring.yaml").read_text()


def test_chart_values_schema_and_component_templates() -> None:
    schema = json.loads((ROOT / "helm/aegisrag/values.schema.json").read_text())
    assert (
        schema["properties"]["migration"]["properties"]["expectedRevision"]["const"]
        == check.TARGET_REVISION
    )
    templates = ROOT / "helm/aegisrag/templates"
    for name in ("workers.yaml", "migration.yaml", "minio.yaml", "monitoring.yaml"):
        assert (templates / name).is_file()
    assert "observability.jaeger.enabled" in (templates / "monitoring.yaml").read_text()
    assert "{{- if .Values.jaeger.enabled }}" not in (templates / "services.yaml").read_text()


@pytest.mark.parametrize("bundled", [False, True])
def test_actual_helm_lint_and_template_when_available(tmp_path, bundled: bool) -> None:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("Helm executable unavailable; actual chart lint/render remains unverified")
    chart = ROOT / "helm/aegisrag"
    values = yaml.safe_load((chart / "values.yaml").read_text())
    values["secrets"]["existingSecret"] = "synthetic-existing-secret"
    values["minio"]["endpoint"] = "https://synthetic-objects.invalid"
    if bundled:
        for key in ("postgres", "redis", "minio"):
            values[key]["enabled"] = True
        for item in values["observability"].values():
            item["enabled"] = True
        values["worker"]["durable"].update(enabled=True, factory="synthetic.factory:create")
        values["worker"]["folderSync"].update(
            enabled=True, apiUrl="https://synthetic-api.invalid", tokenSecret="synthetic-token"
        )
    path = tmp_path / "synthetic-values.yaml"
    path.write_text(yaml.safe_dump(values))
    subprocess.run(
        [helm, "lint", str(chart), "-f", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    rendered = subprocess.run(
        [helm, "template", "synthetic", str(chart), "-f", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    objects = [value for value in yaml.safe_load_all(rendered.stdout) if value]
    names = {value["metadata"]["name"] for value in objects}
    assert "synthetic-aegisrag-worker-ingestion" in names
    assert "synthetic-aegisrag-worker-embedding" in names
    assert "synthetic-aegisrag-migration-1" in names
    if bundled:
        for suffix in (
            "worker-durable",
            "worker-folder-sync",
            "minio",
            "queue-metrics",
            "prometheus",
            "grafana",
            "jaeger",
        ):
            assert f"synthetic-aegisrag-{suffix}" in names
    for value in objects:
        if value["kind"] in {"Deployment", "Job"}:
            assert value["spec"]["template"]["spec"]["containers"]
