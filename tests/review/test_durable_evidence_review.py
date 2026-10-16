"""Current-state evidence checks using synthetic repository records only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from packages.agent.adapters.document_evidence import DocumentEvidenceRevalidator
from packages.agent.dto import AgentCitationRef
from packages.agent.runtime import AgentObservationSummary
from packages.common.errors import DomainError
from packages.data.dto import ChunkRecord, DocumentRecord, DocumentVersionRecord
from tests.unit.agent.test_agent_run_service import _context


class Documents:
    def __init__(self) -> None:
        self.document = DocumentRecord(
            id="document-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status="active",
            source_type="txt",
            source_uri=None,
            title="Synthetic policy",
            checksum="document-checksum",
            acl={"visibility": "tenant"},
            metadata={"latest_version_id": "version-1"},
        )
        self.version = DocumentVersionRecord(
            id="version-1",
            document_id="document-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status="retrieval_ready",
            source_type="txt",
            source_uri=None,
            object_key="synthetic-object",
            filename="policy.txt",
            content_type="text/plain",
            byte_size=24,
            checksum="version-checksum",
            acl={"visibility": "tenant"},
        )
        self.chunk = ChunkRecord(
            tenant_id="tenant-1",
            document_id="document-1",
            version_id="version-1",
            chunk_id="chunk-1",
            created_by="user-1",
            status="active",
            source_type="txt",
            title_path=["Synthetic policy"],
            content="Synthetic current policy text",
            token_count=5,
            checksum="chunk-checksum",
            section_ids=["section-1"],
            acl={"visibility": "tenant"},
        )

    async def get_document(self, **kwargs: Any) -> DocumentRecord:
        assert kwargs == {"tenant_id": "tenant-1", "document_id": "document-1"}
        return self.document

    async def get_version(self, **kwargs: Any) -> DocumentVersionRecord:
        assert kwargs == {"tenant_id": "tenant-1", "version_id": "version-1"}
        return self.version

    async def get_chunk(self, **kwargs: Any) -> ChunkRecord:
        assert kwargs == {
            "tenant_id": "tenant-1",
            "document_id": "document-1",
            "version_id": "version-1",
            "chunk_id": "chunk-1",
        }
        return self.chunk


def observation() -> AgentObservationSummary:
    return AgentObservationSummary(
        tool_name="rag_search",
        status="success",
        latency_ms=1,
        citation_refs=(
            AgentCitationRef(
                document_id="document-1",
                version_id="version-1",
                chunk_id="chunk-1",
            ),
        ),
    )


@pytest.mark.parametrize("layer", ["document", "version", "chunk"])
@pytest.mark.parametrize("change", ["acl", "deleted_at"])
async def test_each_live_evidence_layer_rejects_revocation_or_deletion(
    layer: str,
    change: str,
) -> None:
    repository = Documents()
    validator = DocumentEvidenceRevalidator(repository=repository)
    context = _context(permissions=("agent:run", "document:read", "retrieval:query"))
    initial = await validator.rehydrate(
        context=context, observations=(observation(),), fingerprints={}
    )
    record = getattr(repository, layer)
    replacement = (
        {"acl": {"visibility": "private", "denied_users": ["user-1"]}}
        if change == "acl"
        else {"deleted_at": datetime.now(UTC)}
    )
    setattr(repository, layer, record.model_copy(update=replacement))
    with pytest.raises(DomainError, match="DURABLE_EVIDENCE_REVOKED"):
        await validator.rehydrate(
            context=context,
            observations=(observation(),),
            fingerprints=initial.fingerprints,
        )


@pytest.mark.parametrize("change", ["content", "checksum", "latest_version"])
async def test_pinned_evidence_rejects_changed_content_or_replaced_version(change: str) -> None:
    repository = Documents()
    validator = DocumentEvidenceRevalidator(repository=repository)
    context = _context(permissions=("agent:run", "document:read", "retrieval:query"))
    initial = await validator.rehydrate(
        context=context, observations=(observation(),), fingerprints={}
    )
    if change == "latest_version":
        repository.document = repository.document.model_copy(
            update={
                "metadata": {"latest_version_id": "version-2"},
            }
        )
    else:
        repository.chunk = repository.chunk.model_copy(update={change: "Changed synthetic value"})
    with pytest.raises(DomainError, match="DURABLE_EVIDENCE_STALE"):
        await validator.rehydrate(
            context=context,
            observations=(observation(),),
            fingerprints=initial.fingerprints,
        )


async def test_rehydrated_evidence_is_transient_and_rechecks_query_permission() -> None:
    repository = Documents()
    validator = DocumentEvidenceRevalidator(repository=repository)
    context = _context(permissions=("agent:run", "document:read", "retrieval:query"))
    initial = await validator.rehydrate(
        context=context, observations=(observation(),), fingerprints={}
    )
    assert "Synthetic current policy text" in str(initial.observations[0].prompt_context)
    assert "Synthetic current policy text" not in initial.observations[0].model_dump_json()
    with pytest.raises(DomainError, match="DURABLE_EVIDENCE_REVOKED"):
        await validator.rehydrate(
            context=_context(permissions=("agent:run",)),
            observations=(observation(),),
            fingerprints=initial.fingerprints,
        )
