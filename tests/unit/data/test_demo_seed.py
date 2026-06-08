from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.data.demo_seed import (
    DemoGovernanceStore,
    DemoNamespaceStore,
    DemoSeedError,
    DemoSeedOrchestrator,
    build_seed_plan,
    load_demo_manifest,
    write_seed_report,
)
from packages.data.dto import UploadDocumentCommand, UploadDocumentResult

MANIFEST_PATH = Path("docs/demo/enterprise-rag/manifest.json")


class FakeUploadService:
    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, UploadDocumentCommand]] = []

    async def upload(
        self,
        context: AuthenticatedRequestContext,
        command: UploadDocumentCommand,
    ) -> UploadDocumentResult:
        self.calls.append((context, command))
        return UploadDocumentResult(
            document_id=command.document_id or f"uploaded-{len(self.calls)}",
            version_id=command.version_id or f"version-{len(self.calls)}",
            job_id=f"job-{len(self.calls)}",
            status="uploaded",
        )


class InMemoryDemoNamespaceStore(DemoNamespaceStore):
    def __init__(self, existing_document_ids: Sequence[str] = ()) -> None:
        self.existing_document_ids = set(existing_document_ids)
        self.reset_namespaces: list[str] = []
        self.recorded_document_ids: list[str] = []

    async def reset_namespace(self, namespace: str) -> None:
        self.reset_namespaces.append(namespace)
        self.existing_document_ids.clear()

    async def document_exists(self, *, namespace: str, document_id: str) -> bool:
        _ = namespace
        return document_id in self.existing_document_ids

    async def record_document_seeded(self, *, namespace: str, document_id: str) -> None:
        _ = namespace
        self.existing_document_ids.add(document_id)
        self.recorded_document_ids.append(document_id)


class InMemoryDemoGovernanceStore(DemoGovernanceStore):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def upsert_tenant(self, *, tenant_id: str, display_name: str) -> None:
        self.calls.append(("tenant", {"tenant_id": tenant_id, "display_name": display_name}))

    async def upsert_role(
        self,
        *,
        tenant_id: str,
        role_id: str,
        permissions: tuple[str, ...],
    ) -> None:
        self.calls.append(
            (
                "role",
                {"tenant_id": tenant_id, "role_id": role_id, "permissions": permissions},
            )
        )

    async def upsert_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        roles: tuple[str, ...],
        department: str | None,
        permissions: tuple[str, ...],
    ) -> None:
        self.calls.append(
            (
                "user",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "roles": roles,
                    "department": department,
                    "permissions": permissions,
                },
            )
        )

    async def assign_role(self, *, tenant_id: str, user_id: str, role_id: str) -> None:
        self.calls.append(
            ("assignment", {"tenant_id": tenant_id, "user_id": user_id, "role_id": role_id})
        )


def test_manifest_loads_synthetic_corpus_and_required_cases() -> None:
    manifest = load_demo_manifest(MANIFEST_PATH)

    categories = {document.category for document in manifest.documents}
    assert categories == {"policy", "faq", "product_manual", "technical_doc"}
    assert {case.case_type for case in manifest.cases} >= {
        "answerable",
        "no_answer",
        "acl_isolation",
        "prompt_injection",
        "source_resolve",
    }
    assert all(
        document.source_uri.startswith("synthetic://enterprise-rag-demo/")
        for document in manifest.documents
    )
    assert all(not Path(document.path).is_absolute() for document in manifest.documents)
    expected_chunk_ids = {
        chunk_id for document in manifest.documents for chunk_id in document.expected_chunks
    }
    assert all(
        citation.chunk_id in expected_chunk_ids
        for case in manifest.cases
        for citation in (*case.expected_citations, *case.forbidden_citations)
    )


def test_manifest_rejects_unsafe_source_uri_and_secret_markers(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "policy.md").write_text(
        "Synthetic policy with sk-demo-secret marker.",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "manifest_version": "enterprise-rag-demo-v1",
          "namespace": "enterprise-rag-demo",
          "tenants": [{"tenant_id": "tenant-demo-alpha", "display_name": "Demo Alpha"}],
          "users": [{
            "user_id": "demo-user-admin",
            "tenant_id": "tenant-demo-alpha",
            "roles": ["knowledge_admin"],
            "permissions": ["document:upload", "document:manage"]
          }],
          "roles": [{"role_id": "knowledge_admin", "permissions": ["document:upload"]}],
          "documents": [{
            "document_id": "doc-demo-policy",
            "title": "Policy",
            "category": "policy",
            "path": "corpus/policy.md",
            "source_type": "markdown",
            "source_uri": "file:///C:/real/company/policy.md",
            "acl": {"visibility": "tenant"},
            "expected_chunks": ["chunk-demo-policy"]
          }],
          "cases": []
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(DemoSeedError) as exc_info:
        load_demo_manifest(manifest_path)

    assert exc_info.value.code == "unsafe_manifest"
    assert "sk-demo-secret" not in str(exc_info.value.details)
    assert "C:/real/company" not in str(exc_info.value.details)


def test_manifest_rejects_user_role_references_that_are_not_defined(tmp_path: Path) -> None:
    manifest_payload = load_demo_manifest(MANIFEST_PATH).model_dump(mode="json")
    manifest_payload["users"][0]["roles"] = ["missing-role"]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    source_root = MANIFEST_PATH.parent
    for document in manifest_payload["documents"]:
        source = source_root / document["path"]
        target = tmp_path / document["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(DemoSeedError) as exc_info:
        load_demo_manifest(manifest_path)

    assert exc_info.value.code == "unsafe_manifest"


def test_build_seed_plan_reports_safe_counts_without_queries_or_raw_sources() -> None:
    manifest = load_demo_manifest(MANIFEST_PATH)

    plan = build_seed_plan(manifest)
    payload = plan.model_dump(mode="json")

    assert payload["namespace"] == "enterprise-rag-demo"
    assert payload["tenant_count"] == 1
    assert payload["document_count"] == 4
    assert payload["case_count"] >= 7
    assert "source_uri" not in str(payload)
    assert "query" not in str(payload).lower()


@pytest.mark.asyncio
async def test_seed_orchestrator_uses_upload_service_and_is_idempotent() -> None:
    manifest = load_demo_manifest(MANIFEST_PATH)
    upload_service = FakeUploadService()
    store = InMemoryDemoNamespaceStore()
    orchestrator = DemoSeedOrchestrator(
        upload_service=upload_service,
        namespace_store=store,
    )
    admin_context = AuthenticatedRequestContext(
        request_id="req-demo-seed",
        trace_id="trace-demo-seed",
        auth=AuthContext(
            user_id="demo-user-admin",
            tenant_id="tenant-demo-alpha",
            roles=("knowledge_admin",),
            permissions=("document:upload", "document:manage"),
        ),
    )

    first = await orchestrator.seed(manifest, context=admin_context)
    second = await orchestrator.seed(manifest, context=admin_context)

    assert first.created_document_count == len(manifest.documents)
    assert second.created_document_count == 0
    assert second.skipped_document_count == len(manifest.documents)
    assert len(upload_service.calls) == len(manifest.documents)
    context, command = upload_service.calls[0]
    assert context.auth.tenant_id == "tenant-demo-alpha"
    document_id = command.document_id
    assert document_id is not None
    assert document_id.startswith("doc-demo-")
    assert command.version_id is not None
    assert command.version_id.startswith("ver-demo-")
    source_uri = command.source_uri
    assert source_uri is not None
    assert source_uri.startswith("synthetic://enterprise-rag-demo/")
    assert command.acl["visibility"] in {"tenant", "private", "restricted"}


@pytest.mark.asyncio
async def test_seed_orchestrator_rejects_context_tenant_mismatch() -> None:
    manifest = load_demo_manifest(MANIFEST_PATH)
    orchestrator = DemoSeedOrchestrator(
        upload_service=FakeUploadService(),
        namespace_store=InMemoryDemoNamespaceStore(),
    )
    context = AuthenticatedRequestContext(
        request_id="req-demo-wrong-tenant",
        trace_id="trace-demo-wrong-tenant",
        auth=AuthContext(
            user_id="demo-user-admin",
            tenant_id="tenant-other",
            roles=("knowledge_admin",),
            permissions=("document:upload", "document:manage"),
        ),
    )

    with pytest.raises(DemoSeedError) as exc_info:
        await orchestrator.seed(manifest, context=context)

    assert exc_info.value.code == "tenant_mismatch"
    assert "tenant-other" in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_seed_orchestrator_upserts_demo_governance_before_uploads() -> None:
    manifest = load_demo_manifest(MANIFEST_PATH)
    upload_service = FakeUploadService()
    governance = InMemoryDemoGovernanceStore()
    orchestrator = DemoSeedOrchestrator(
        upload_service=upload_service,
        namespace_store=InMemoryDemoNamespaceStore(),
        governance_store=governance,
    )
    context = AuthenticatedRequestContext(
        request_id="req-demo-governance",
        trace_id="trace-demo-governance",
        auth=AuthContext(
            user_id="demo-user-admin",
            tenant_id="tenant-demo-alpha",
            roles=("knowledge_admin",),
            permissions=("document:upload", "document:manage"),
        ),
    )

    await orchestrator.seed(manifest, context=context)

    assert governance.calls[0] == (
        "tenant",
        {"tenant_id": "tenant-demo-alpha", "display_name": "Demo Alpha"},
    )
    assert sum(1 for kind, _payload in governance.calls if kind == "role") == len(manifest.roles)
    assert sum(1 for kind, _payload in governance.calls if kind == "user") == len(manifest.users)
    assert any(
        kind == "assignment"
        and payload == {
            "tenant_id": "tenant-demo-alpha",
            "user_id": "demo-user-admin",
            "role_id": "knowledge_admin",
        }
        for kind, payload in governance.calls
    )
    assert len(upload_service.calls) == len(manifest.documents)


@pytest.mark.asyncio
async def test_seed_orchestrator_reset_scope_only_targets_demo_namespace() -> None:
    manifest = load_demo_manifest(MANIFEST_PATH)
    upload_service = FakeUploadService()
    store = InMemoryDemoNamespaceStore(
        existing_document_ids=[doc.document_id for doc in manifest.documents]
    )
    orchestrator = DemoSeedOrchestrator(
        upload_service=upload_service,
        namespace_store=store,
    )
    context = AuthenticatedRequestContext(
        request_id="req-demo-seed-reset",
        trace_id="trace-demo-seed-reset",
        auth=AuthContext(
            user_id="demo-user-admin",
            tenant_id="tenant-demo-alpha",
            roles=("knowledge_admin",),
            permissions=("document:upload", "document:manage"),
        ),
    )

    result = await orchestrator.seed(manifest, context=context, reset_demo_namespace=True)

    assert store.reset_namespaces == ["enterprise-rag-demo"]
    assert result.created_document_count == len(manifest.documents)
    assert len(upload_service.calls) == len(manifest.documents)


def test_write_seed_report_redacts_unsafe_failure_details(tmp_path: Path) -> None:
    report_path = tmp_path / "seed-report.json"

    write_seed_report(
        report_path,
        {
            "namespace": "enterprise-rag-demo",
            "status": "failed",
            "request_id": "req-demo",
            "trace_id": "trace-demo",
            "failure": {
                "stage": "upload",
                "Authorization": "Bearer should-not-appear",
                "source_uri": "synthetic://enterprise-rag-demo/hr-leave-policy",
                "raw_source_uri": "synthetic://enterprise-rag-demo/hr-leave-policy",
                "database_password": "local-password",
                "query": "full question should not be stored",
                "next_steps": [
                    "python -m packages.data.demo_seed validate "
                    "--manifest docs/demo/enterprise-rag/manifest.json"
                ],
            },
        },
    )

    text = report_path.read_text(encoding="utf-8")
    assert "Bearer should-not-appear" not in text
    assert "source_uri" not in text
    assert "raw_source_uri" not in text
    assert "local-password" not in text
    assert "full question" not in text
    assert "next_steps" in text
    assert "demo_seed validate" in text
