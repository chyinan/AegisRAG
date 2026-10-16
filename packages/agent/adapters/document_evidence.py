"""Adapt current document/ACL policy to the durable evidence port."""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

from packages.agent.durable.dto import durable_error
from packages.agent.durable.evidence import RehydratedEvidence
from packages.agent.runtime import AgentObservationSummary
from packages.auth.policies import has_rag_query_permission
from packages.common.context import AuthenticatedRequestContext
from packages.common.logging import redact_sensitive_data
from packages.data.dto import ChunkRecord, DocumentRecord, DocumentVersionRecord
from packages.rag.access import acl_allows_auth


class EvidenceRepository(Protocol):
    async def get_document(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None: ...
    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None: ...
    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None: ...


class DocumentEvidenceRevalidator:
    def __init__(self, *, repository: EvidenceRepository) -> None:
        self._repository = repository

    async def rehydrate(
        self,
        *,
        context: AuthenticatedRequestContext,
        observations: Sequence[AgentObservationSummary],
        fingerprints: dict[str, str],
    ) -> RehydratedEvidence:
        restored: list[AgentObservationSummary] = []
        current = dict(fingerprints)
        for observation in observations:
            if observation.tool_name == "calculator":
                restored.append(
                    observation.model_copy(
                        update={
                            "prompt_context": dict(observation.output_summary),
                        }
                    )
                )
                continue
            if not observation.citation_refs:
                restored.append(observation.model_copy(update={"prompt_context": {}}))
                continue
            if not has_rag_query_permission(context.auth):
                raise durable_error("DURABLE_EVIDENCE_REVOKED", status_code=403)
            items: list[dict[str, object]] = []
            for citation in observation.citation_refs:
                document = await self._repository.get_document(
                    tenant_id=context.auth.tenant_id,
                    document_id=citation.document_id,
                )
                version = await self._repository.get_version(
                    tenant_id=context.auth.tenant_id,
                    version_id=citation.version_id,
                )
                chunk = await self._repository.get_chunk(
                    tenant_id=context.auth.tenant_id,
                    document_id=citation.document_id,
                    version_id=citation.version_id,
                    chunk_id=citation.chunk_id,
                )
                if document is None or version is None or chunk is None:
                    raise durable_error("DURABLE_EVIDENCE_REVOKED", status_code=403)
                if (
                    document.tenant_id != context.auth.tenant_id
                    or version.tenant_id != context.auth.tenant_id
                    or chunk.tenant_id != context.auth.tenant_id
                    or document.id != citation.document_id
                    or version.id != citation.version_id
                    or version.document_id != document.id
                    or chunk.document_id != document.id
                    or chunk.version_id != version.id
                    or chunk.chunk_id != citation.chunk_id
                    or document.deleted_at is not None
                    or document.status == "deleted"
                    or version.deleted_at is not None
                    or version.status != "retrieval_ready"
                    or chunk.deleted_at is not None
                    or chunk.status != "active"
                    or chunk.page_start != citation.page_start
                    or chunk.page_end != citation.page_end
                    or not acl_allows_auth(document.acl, context.auth)
                    or not acl_allows_auth(version.acl, context.auth)
                    or not acl_allows_auth(chunk.acl, context.auth)
                ):
                    raise durable_error("DURABLE_EVIDENCE_REVOKED", status_code=403)
                latest = document.metadata.get("latest_version_id")
                if latest is not None and latest != version.id:
                    raise durable_error("DURABLE_EVIDENCE_STALE")
                key = hashlib.sha256(
                    f"{document.id}:{version.id}:{chunk.chunk_id}".encode()
                ).hexdigest()
                digest = hashlib.sha256(
                    f"{document.checksum}:{version.checksum}:{chunk.checksum}:{chunk.content}".encode()
                ).hexdigest()
                if key in current and current[key] != digest:
                    raise durable_error("DURABLE_EVIDENCE_STALE")
                current[key] = digest
                excerpt = redact_sensitive_data(chunk.content[:1200])
                items.append(
                    {
                        "document_id": document.id,
                        "version_id": version.id,
                        "chunk_id": chunk.chunk_id,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "content_excerpt": excerpt if isinstance(excerpt, str) else "",
                    }
                )
            restored.append(
                observation.model_copy(
                    update={
                        "prompt_context": {
                            "status": observation.result_status or "success",
                            "result_count": len(items),
                            "results": items[:5],
                        }
                    }
                )
            )
        return RehydratedEvidence(tuple(restored), current)
