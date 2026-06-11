from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import (
    ChunkRecord,
    DocumentVersionRecord,
    EmbeddingJobRecord,
    EnqueuedJob,
    IngestionJobRecord,
    StoredDocumentContent,
)
from packages.data.exceptions import DocumentStorageReadError
from packages.data.queue.contracts import QueuePayload
from packages.ingestion.domain import ParsedDocument, ParseRequest, Section
from packages.ingestion.exceptions import DocumentParseError
from packages.ingestion.parsers.registry import ParserRegistry
from packages.ingestion.service import IngestionParseService


def _minimal_pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
        "/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream",
    ]
    output = BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{object_number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref_offset = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return output.getvalue()


def _docx_bytes(text: str) -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.'
        'main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


@dataclass
class FakeLogger:
    events: list[tuple[str, dict[str, object]]]

    def info(self, event: str, **kwargs: object) -> object:
        self.events.append((event, kwargs))
        return None


class FakeStorage:
    def __init__(
        self,
        *,
        content: bytes = b"# Policy\nBody",
        fail: bool = False,
        byte_size: int | None = None,
        checksum: str = "checksum-1",
    ) -> None:
        self.content = content
        self.fail = fail
        self.byte_size = byte_size
        self.checksum = checksum
        self.calls: list[dict[str, str]] = []

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> StoredDocumentContent:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "version_id": version_id,
                "object_key": object_key,
            }
        )
        if self.fail:
            raise DocumentStorageReadError(details={"document_id": document_id})
        return StoredDocumentContent(
            bucket="documents",
            object_key=object_key,
            content=self.content,
            byte_size=self.byte_size if self.byte_size is not None else len(self.content),
            checksum=self.checksum,
        )


class FakeRepository:
    def __init__(
        self,
        *,
        source_type: str = "markdown",
        job_status: str = "queued",
        version_metadata: dict[str, object] | None = None,
        byte_size: int = len(b"# Policy\nBody"),
    ) -> None:
        self.job = IngestionJobRecord(
            id="job-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status=job_status,
            document_id="doc-1",
            version_id="ver-1",
            queue_name="ingestion",
        )
        self.version = DocumentVersionRecord(
            id="ver-1",
            document_id="doc-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status="uploaded",
            source_type=source_type,
            source_uri="kb://policy.md",
            object_key="raw/tenant-1/doc-1/ver-1/policy.md",
            filename="policy.md",
            content_type="text/markdown",
            byte_size=byte_size,
            acl={"visibility": "tenant"},
            checksum="checksum-1",
            metadata=version_metadata if version_metadata is not None else {"department": "HR"},
        )
        self.parsing: list[str] = []
        self.parsed: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.claims: list[dict[str, object]] = []
        self.chunks: list[ChunkRecord] = []
        self.chunked: list[dict[str, object]] = []
        self.embedding_jobs: list[EmbeddingJobRecord] = []
        self.commits = 0
        self.rollbacks = 0

    async def get_ingestion_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord | None:
        if tenant_id != self.job.tenant_id or job_id != self.job.id:
            return None
        return self.job

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        if tenant_id != self.version.tenant_id or version_id != self.version.id:
            return None
        return self.version

    async def mark_ingestion_job_parsing(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord:
        self.parsing.append(job_id)
        self.job = self.job.model_copy(update={"status": "parsing", "error_code": None})
        return self.job

    async def claim_ingestion_job_parsing(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> IngestionJobRecord | None:
        self.claims.append(
            {
                "tenant_id": tenant_id,
                "job_id": job_id,
                "document_id": document_id,
                "version_id": version_id,
                "stale_before": stale_before,
            }
        )
        if (
            tenant_id != self.job.tenant_id
            or job_id != self.job.id
            or document_id != self.job.document_id
            or version_id != self.job.version_id
        ):
            return None
        is_startable = self.job.status in {"uploaded", "queued", "failed_retryable"}
        is_stale_parsing = (
            stale_before is not None
            and self.job.status == "parsing"
            and self.job.last_attempt_at is not None
            and self.job.last_attempt_at < stale_before
        )
        if not is_startable and not is_stale_parsing:
            return None
        self.parsing.append(job_id)
        self.job = self.job.model_copy(
            update={
                "status": "parsing",
                "error_code": None,
                "attempt_count": self.job.attempt_count + 1,
                "last_attempt_at": datetime.now(tz=UTC),
            }
        )
        return self.job

    async def mark_ingestion_job_parsed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        parsed_metadata: dict[str, object],
    ) -> IngestionJobRecord:
        self.parsed.append(parsed_metadata)
        self.job = self.job.model_copy(update={"status": "parsed", "error_code": None})
        self.version = self.version.model_copy(update={"status": "parsed"})
        return self.job

    async def replace_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        chunks: list[ChunkRecord],
    ) -> list[ChunkRecord]:
        self.chunks = chunks
        return chunks

    async def mark_ingestion_job_chunked(
        self,
        *,
        tenant_id: str,
        job_id: str,
        chunk_metadata: dict[str, object],
    ) -> IngestionJobRecord:
        self.chunked.append(chunk_metadata)
        self.job = self.job.model_copy(update={"status": "chunked", "error_code": None})
        self.version = self.version.model_copy(update={"status": "chunked"})
        return self.job

    async def create_embedding_job(
        self,
        *,
        job: EmbeddingJobRecord,
    ) -> EmbeddingJobRecord:
        self.embedding_jobs.append(job)
        return job

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str = "failed_retryable",
    ) -> IngestionJobRecord:
        self.failed.append({"job_id": job_id, "status": status, "error_code": error_code})
        self.job = self.job.model_copy(update={"status": status, "error_code": error_code})
        return self.job

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(user_id="user-1", tenant_id="tenant-1"),
    )


def _service(
    *,
    repository: FakeRepository | None = None,
    storage: FakeStorage | None = None,
    audit: InMemoryAuditPort | None = None,
    logger: FakeLogger | None = None,
) -> tuple[IngestionParseService, FakeRepository, FakeStorage, InMemoryAuditPort, FakeLogger]:
    resolved_repository = repository or FakeRepository()
    resolved_storage = storage or FakeStorage()
    resolved_audit = audit or InMemoryAuditPort()
    resolved_logger = logger or FakeLogger(events=[])
    return (
        IngestionParseService(
            repository=resolved_repository,
            object_storage=resolved_storage,
            audit=resolved_audit,
            logger=resolved_logger,
        ),
        resolved_repository,
        resolved_storage,
        resolved_audit,
        resolved_logger,
    )


class ExplodingParser:
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        raise RuntimeError("parser implementation bug")


class MismatchedParser:
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        section = Section(
            section_id="other-ver:section-1",
            tenant_id=request.tenant_id,
            document_id="other-doc",
            version_id=request.version_id,
            source_type=request.source_type,
            title_path=["Bad"],
            content="bad",
            acl=request.acl,
        )
        return ParsedDocument(
            tenant_id=request.tenant_id,
            document_id="other-doc",
            version_id=request.version_id,
            source_type=request.source_type,
            sections=[section],
            acl=request.acl,
            checksum=request.checksum,
        )


class SectionMismatchedParser:
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        section = Section(
            section_id=f"{request.version_id}:section-1",
            tenant_id="other-tenant",
            document_id=request.document_id,
            version_id=request.version_id,
            source_type=request.source_type,
            title_path=["Bad"],
            content="bad",
            acl={"visibility": "public"},
        )
        return ParsedDocument(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type=request.source_type,
            sections=[section],
            acl=request.acl,
            checksum=request.checksum,
        )


class FakeEmbeddingQueue:
    def __init__(self) -> None:
        self.payloads: list[QueuePayload] = []

    async def enqueue_embedding_job(self, payload: QueuePayload) -> EnqueuedJob:
        self.payloads.append(payload)
        return EnqueuedJob(queue_job_id="rq-embedding-1", queue_name="embedding")


@pytest.mark.asyncio
async def test_parse_service_marks_job_parsed_and_records_safe_summary() -> None:
    service, repository, storage, audit, logger = _service()

    result = await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "parsed"
    assert storage.calls == [
        {
            "tenant_id": "tenant-1",
            "document_id": "doc-1",
            "version_id": "ver-1",
            "object_key": "raw/tenant-1/doc-1/ver-1/policy.md",
        }
    ]
    assert repository.parsing == ["job-1"]
    assert repository.parsed[0]["section_count"] == 1
    assert "title_paths" not in repository.parsed[0]
    assert repository.commits == 2
    assert audit.events[-1].status is AuditStatus.SUCCESS
    combined = f"{audit.events!r} {logger.events!r}"
    assert "Body" not in combined
    assert "# Policy" not in combined


@pytest.mark.asyncio
async def test_parse_service_chunks_and_enqueues_embedding_when_pipeline_enabled() -> None:
    embedding_queue = FakeEmbeddingQueue()
    service, repository, _storage, audit, logger = _service()
    service = IngestionParseService(
        repository=repository,
        object_storage=FakeStorage(),
        audit=audit,
        embedding_queue=embedding_queue,
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        embedding_version="test-version",
        embedding_dim=768,
        id_factory=lambda: "embedding-job-1",
        logger=logger,
    )

    result = await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "chunked"
    assert result.chunk_count == 1
    assert result.embedding_job_id == "embedding-job-1"
    assert repository.chunked[0]["chunk_count"] == 1
    assert repository.embedding_jobs == [
        EmbeddingJobRecord(
            id="embedding-job-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status="queued",
            document_id="doc-1",
            version_id="ver-1",
            provider="ollama",
            model="nomic-embed-text",
            version="test-version",
            dim=768,
            chunk_count=1,
            metadata={
                "source_ingestion_job_id": "job-1",
                "chunk_artifact_summary": repository.chunked[0],
            },
        )
    ]
    assert embedding_queue.payloads[0].job_type == "embedding.embed_document"
    assert embedding_queue.payloads[0].resource_id == "embedding-job-1"
    assert embedding_queue.payloads[0].parameters == {
        "document_id": "doc-1",
        "version_id": "ver-1",
    }
    combined = (
        f"{repository.chunked!r} {repository.embedding_jobs!r} "
        f"{audit.events!r} {logger.events!r}"
    )
    assert "Body" not in combined
    assert "# Policy" not in combined


@pytest.mark.asyncio
async def test_parse_service_parses_pdf_and_records_page_summary_without_text() -> None:
    content = _minimal_pdf_bytes("Confidential page text")
    service, repository, _storage, audit, logger = _service(
        repository=FakeRepository(source_type="pdf", byte_size=len(content)),
        storage=FakeStorage(content=content, byte_size=len(content)),
    )

    result = await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "parsed"
    assert repository.parsed[0]["page_count"] == 1
    assert repository.parsed[0]["page_ranges"] == [[1, 1]]
    combined = f"{repository.parsed!r} {audit.events!r} {logger.events!r}"
    assert "Confidential page text" not in combined


@pytest.mark.asyncio
async def test_parse_service_parses_docx_and_records_safe_summary() -> None:
    content = _docx_bytes("Confidential paragraph")
    service, repository, _storage, audit, logger = _service(
        repository=FakeRepository(source_type="docx", byte_size=len(content)),
        storage=FakeStorage(content=content, byte_size=len(content)),
    )

    result = await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "parsed"
    assert repository.parsed[0]["page_metadata"] == "unavailable"
    assert repository.parsed[0]["heading_count"] == 0
    combined = f"{repository.parsed!r} {audit.events!r} {logger.events!r}"
    assert "Confidential paragraph" not in combined


@pytest.mark.asyncio
async def test_parse_service_maps_parser_error_to_terminal_job_failure() -> None:
    service, repository, _storage, audit, _logger = _service(
        repository=FakeRepository(byte_size=0),
        storage=FakeStorage(content=b"", byte_size=0),
    )

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_EMPTY_CONTENT"
    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_terminal",
            "error_code": "DOCUMENT_PARSE_EMPTY_CONTENT",
        }
    ]
    assert repository.commits == 2
    assert audit.events[-1].status is AuditStatus.FAILURE
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_EMPTY_CONTENT"


@pytest.mark.asyncio
async def test_parse_service_maps_storage_read_error_to_retryable_failure() -> None:
    service, repository, _storage, audit, _logger = _service(storage=FakeStorage(fail=True))

    with pytest.raises(DocumentStorageReadError):
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_retryable",
            "error_code": "DOCUMENT_STORAGE_READ_FAILED",
        }
    ]
    assert audit.events[-1].error_code == "DOCUMENT_STORAGE_READ_FAILED"


@pytest.mark.asyncio
async def test_parse_service_reports_unsupported_type_before_reading_object_storage() -> None:
    service, repository, storage, audit, _logger = _service(
        repository=FakeRepository(source_type="rtf"),
        storage=FakeStorage(fail=True),
    )

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_UNSUPPORTED_TYPE"
    assert storage.calls == []
    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_terminal",
            "error_code": "DOCUMENT_PARSE_UNSUPPORTED_TYPE",
        }
    ]
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_UNSUPPORTED_TYPE"


@pytest.mark.asyncio
async def test_parse_service_maps_unexpected_parser_error_to_retryable_failure() -> None:
    service, repository, _storage, audit, _logger = _service()
    service = IngestionParseService(
        repository=repository,
        object_storage=FakeStorage(),
        audit=audit,
        parser_registry=ParserRegistry({"markdown": ExplodingParser()}),
    )

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_retryable",
            "error_code": "DOCUMENT_PARSE_FAILED",
        }
    ]
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"


@pytest.mark.asyncio
async def test_parse_service_rejects_object_content_that_does_not_match_version_record() -> None:
    service, repository, _storage, audit, _logger = _service(
        storage=FakeStorage(byte_size=999, checksum="other-checksum")
    )

    with pytest.raises(DocumentStorageReadError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_STORAGE_READ_FAILED"
    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_retryable",
            "error_code": "DOCUMENT_STORAGE_READ_FAILED",
        }
    ]
    assert audit.events[-1].error_code == "DOCUMENT_STORAGE_READ_FAILED"


@pytest.mark.asyncio
async def test_parse_service_returns_idempotent_result_for_already_parsed_job() -> None:
    service, repository, storage, _audit, _logger = _service(
        repository=FakeRepository(
            job_status="parsed",
            version_metadata={"parsed_artifact_summary": {"section_count": 3}},
        )
    )

    result = await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "parsed"
    assert result.section_count == 3
    assert storage.calls == []
    assert repository.parsing == []


@pytest.mark.asyncio
async def test_parse_service_does_not_restart_terminal_failed_job() -> None:
    service, repository, storage, _audit, _logger = _service(
        repository=FakeRepository(job_status="failed_terminal")
    )

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert storage.calls == []
    assert repository.parsing == []
    assert repository.failed == []


@pytest.mark.asyncio
async def test_parse_service_rejects_existing_job_when_payload_ids_do_not_match() -> None:
    service, repository, _storage, audit, _logger = _service()

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="other-doc",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.failed == []
    assert repository.parsing == []
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"


@pytest.mark.asyncio
async def test_parse_service_rejects_parser_result_for_different_document() -> None:
    service, repository, _storage, audit, _logger = _service()
    service = IngestionParseService(
        repository=repository,
        object_storage=FakeStorage(),
        audit=audit,
        parser_registry=ParserRegistry({"markdown": MismatchedParser()}),
    )

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_retryable",
            "error_code": "DOCUMENT_PARSE_FAILED",
        }
    ]
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"


@pytest.mark.asyncio
async def test_parse_service_rejects_payload_ids_that_do_not_match_job() -> None:
    service, repository, _storage, _audit, _logger = _service()

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="other-doc",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.parsing == []


@pytest.mark.asyncio
async def test_parse_service_does_not_fail_job_when_payload_ids_do_not_match() -> None:
    service, repository, storage, audit, _logger = _service()

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="other-doc",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.failed == []
    assert repository.parsing == []
    assert storage.calls == []
    assert audit.events[-1].status is AuditStatus.FAILURE
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"


@pytest.mark.asyncio
async def test_parse_service_retries_stale_parsing_jobs_through_claim() -> None:
    stale_time = datetime.now(tz=UTC) - timedelta(hours=1)
    repository = FakeRepository(job_status="parsing")
    repository.job = repository.job.model_copy(update={"last_attempt_at": stale_time})
    service, repository, storage, _audit, _logger = _service(repository=repository)

    result = await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "parsed"
    assert repository.claims
    assert repository.parsing == ["job-1"]
    assert storage.calls


@pytest.mark.asyncio
async def test_parse_service_does_not_start_active_parsing_job() -> None:
    recent_time = datetime.now(tz=UTC)
    repository = FakeRepository(job_status="parsing")
    repository.job = repository.job.model_copy(update={"last_attempt_at": recent_time})
    service, repository, storage, _audit, _logger = _service(repository=repository)

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.parsing == []
    assert repository.failed == []
    assert storage.calls == []


@pytest.mark.asyncio
async def test_parse_service_rejects_parser_sections_for_different_scope_or_acl() -> None:
    service, repository, _storage, audit, _logger = _service()
    service = IngestionParseService(
        repository=repository,
        object_storage=FakeStorage(),
        audit=audit,
        parser_registry=ParserRegistry({"markdown": SectionMismatchedParser()}),
    )

    with pytest.raises(DocumentParseError) as exc_info:
        await service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert repository.failed == [
        {
            "job_id": "job-1",
            "status": "failed_retryable",
            "error_code": "DOCUMENT_PARSE_FAILED",
        }
    ]
    assert audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"


@pytest.mark.asyncio
async def test_parse_service_does_not_persist_or_log_raw_title_paths() -> None:
    content = _docx_bytes("Confidential paragraph")
    service, repository, _storage, audit, logger = _service(
        repository=FakeRepository(
            source_type="docx",
            byte_size=len(content),
            version_metadata={"department": "HR"},
        ),
        storage=FakeStorage(content=content, byte_size=len(content)),
    )

    await service.parse_job(
        _context(),
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert "title_paths" not in repository.parsed[0]
    combined = f"{repository.parsed!r} {audit.events!r} {logger.events!r}"
    assert "policy.md" not in combined
    assert "title_paths" not in combined


@pytest.mark.asyncio
async def test_parse_service_maps_pdf_and_docx_parser_failures_to_job_failures() -> None:
    pdf_service, pdf_repository, _pdf_storage, pdf_audit, _pdf_logger = _service(
        repository=FakeRepository(source_type="pdf", byte_size=len(b"%PDF-bad")),
        storage=FakeStorage(content=b"%PDF-bad", byte_size=len(b"%PDF-bad")),
    )

    with pytest.raises(DocumentParseError) as pdf_error:
        await pdf_service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert pdf_error.value.code == "DOCUMENT_PARSE_FAILED"
    assert pdf_repository.failed[-1] == {
        "job_id": "job-1",
        "status": "failed_retryable",
        "error_code": "DOCUMENT_PARSE_FAILED",
    }
    assert pdf_audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"

    docx_service, docx_repository, _docx_storage, docx_audit, _docx_logger = _service(
        repository=FakeRepository(source_type="docx", byte_size=len(b"not a docx")),
        storage=FakeStorage(content=b"not a docx", byte_size=len(b"not a docx")),
    )

    with pytest.raises(DocumentParseError) as docx_error:
        await docx_service.parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert docx_error.value.code == "DOCUMENT_PARSE_FAILED"
    assert docx_repository.failed[-1] == {
        "job_id": "job-1",
        "status": "failed_retryable",
        "error_code": "DOCUMENT_PARSE_FAILED",
    }
    assert docx_audit.events[-1].error_code == "DOCUMENT_PARSE_FAILED"
