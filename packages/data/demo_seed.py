from __future__ import annotations

import argparse
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import UploadDocumentCommand, UploadDocumentResult

DemoDocumentCategory = Literal["policy", "faq", "product_manual", "technical_doc"]
DemoCaseType = Literal[
    "answerable",
    "no_answer",
    "acl_isolation",
    "prompt_injection",
    "source_resolve",
]

SAFE_DEMO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
SYNTHETIC_SOURCE_PREFIX = "synthetic://enterprise-rag-demo/"
FORBIDDEN_TEXT_MARKERS = (
    "api_key",
    "access_token",
    "bearer ",
    "sk-",
    "-----begin",
    "openwebui_provider_api_key",
    "minio_secret",
)
REPORT_FORBIDDEN_KEYS = {
    "authorization",
    "access_token",
    "jwt",
    "service_token",
    "source_uri",
    "object_key",
    "bucket",
    "query",
    "prompt",
    "chunk",
    "embedding",
    "embeddings",
    "vector",
    "vectors",
    "sql",
    "raw_response",
}


class DemoSeedError(ValueError):
    def __init__(self, *, code: str, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(self.__str__())

    def __str__(self) -> str:
        safe_details = json.dumps(self.details, ensure_ascii=False, sort_keys=True)
        return f"{self.code}: {safe_details}"


class DemoAcl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    visibility: Literal["tenant", "private", "restricted"] = "tenant"
    roles: tuple[str, ...] = ()
    users: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()

    @field_validator("roles", "users", "departments", mode="before")
    @classmethod
    def _tuple(cls, value: object) -> tuple[str, ...]:
        return _text_tuple(value)


class DemoTenant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    display_name: str

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id(cls, value: str) -> str:
        return _safe_id(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return _safe_text(value, field_name="display_name", max_length=120)


class DemoUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    tenant_id: str
    roles: tuple[str, ...]
    department: str | None = None
    permissions: tuple[str, ...]

    @field_validator("user_id", "tenant_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _safe_id(value)

    @field_validator("roles", "permissions", mode="before")
    @classmethod
    def _required_tuple(cls, value: object) -> tuple[str, ...]:
        items = _text_tuple(value)
        if not items:
            raise ValueError("value must not be empty")
        return items

    @field_validator("department")
    @classmethod
    def _department(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_text(value, field_name="department", max_length=80)


class DemoRole(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role_id: str
    permissions: tuple[str, ...]

    @field_validator("role_id")
    @classmethod
    def _role_id(cls, value: str) -> str:
        return _safe_id(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: object) -> tuple[str, ...]:
        items = _text_tuple(value)
        if not items:
            raise ValueError("permissions must not be empty")
        return items


class DemoCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None

    @field_validator("document_id", "version_id", "chunk_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _safe_id(value)

    @model_validator(mode="after")
    def _page_range(self) -> Self:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("page range must be valid")
        return self


class DemoDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    version_id: str
    title: str
    category: DemoDocumentCategory
    path: str
    source_type: Literal["markdown", "txt"]
    source_uri: str
    acl: DemoAcl = Field(default_factory=DemoAcl)
    expected_chunks: tuple[str, ...]

    @field_validator("document_id", "version_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _safe_id(value)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _safe_text(value, field_name="title", max_length=160)

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or WINDOWS_ABSOLUTE_PATH_PATTERN.match(normalized)
        ):
            raise ValueError("path must be relative")
        if ".." in Path(normalized).parts:
            raise ValueError("path must not traverse directories")
        return normalized

    @field_validator("source_uri")
    @classmethod
    def _source_uri(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(SYNTHETIC_SOURCE_PREFIX):
            raise ValueError("source_uri must use controlled synthetic prefix")
        if _looks_like_path_or_token_url(normalized):
            raise ValueError("source_uri must not expose local or object storage locators")
        _reject_forbidden_markers(normalized, "source_uri")
        return normalized

    @field_validator("expected_chunks", mode="before")
    @classmethod
    def _expected_chunks(cls, value: object) -> tuple[str, ...]:
        items = _text_tuple(value)
        if not items:
            raise ValueError("expected_chunks must not be empty")
        return tuple(_safe_id(item) for item in items)


class DemoCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    case_type: DemoCaseType
    category: DemoDocumentCategory
    user_id: str
    query: str
    expected_no_answer: bool = False
    expected_citations: tuple[DemoCitation, ...] = ()
    forbidden_citations: tuple[DemoCitation, ...] = ()
    must_not_include_terms: tuple[str, ...] = ()

    @field_validator("case_id", "user_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _safe_id(value)

    @field_validator("query")
    @classmethod
    def _query(cls, value: str) -> str:
        return _safe_text(value, field_name="query", max_length=500)

    @field_validator("must_not_include_terms", mode="before")
    @classmethod
    def _terms(cls, value: object) -> tuple[str, ...]:
        return _text_tuple(value)

    @model_validator(mode="after")
    def _case_contract(self) -> Self:
        if self.case_type == "no_answer" and not self.expected_no_answer:
            raise ValueError("no_answer cases must set expected_no_answer")
        if (
            self.case_type in {"answerable", "source_resolve", "prompt_injection"}
            and not self.expected_citations
        ):
            raise ValueError("answerable cases must define expected citations")
        if self.case_type == "acl_isolation" and not self.forbidden_citations:
            raise ValueError("acl_isolation cases must define forbidden citations")
        return self


class DemoManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: str
    namespace: str
    tenants: tuple[DemoTenant, ...]
    users: tuple[DemoUser, ...]
    roles: tuple[DemoRole, ...]
    documents: tuple[DemoDocument, ...]
    cases: tuple[DemoCase, ...]
    manifest_path: Path | None = Field(default=None, exclude=True)

    @field_validator("manifest_version", "namespace")
    @classmethod
    def _safe_required(cls, value: str) -> str:
        return _safe_id(value)

    @model_validator(mode="after")
    def _cross_reference_contract(self) -> Self:
        tenant_ids = {tenant.tenant_id for tenant in self.tenants}
        user_ids = {user.user_id for user in self.users}
        document_ids = {document.document_id for document in self.documents}
        citation_keys = {
            (document.document_id, document.version_id, chunk_id)
            for document in self.documents
            for chunk_id in document.expected_chunks
        }
        if len(document_ids) != len(self.documents):
            raise ValueError("duplicate document_id")
        if not tenant_ids or len(tenant_ids) != 1:
            raise ValueError("demo manifest must define exactly one tenant")
        for user in self.users:
            if user.tenant_id not in tenant_ids:
                raise ValueError("user references unknown tenant")
        for case in self.cases:
            if case.user_id not in user_ids:
                raise ValueError("case references unknown user")
            for citation in (*case.expected_citations, *case.forbidden_citations):
                key = (citation.document_id, citation.version_id, citation.chunk_id)
                if citation.document_id not in document_ids or key not in citation_keys:
                    raise ValueError("case citation references unknown document chunk")
        categories = {document.category for document in self.documents}
        if categories != {"policy", "faq", "product_manual", "technical_doc"}:
            raise ValueError("manifest must cover all demo document categories")
        return self


class DemoSeedPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    namespace: str
    tenant_count: int
    user_count: int
    role_count: int
    document_count: int
    case_count: int
    categories: dict[str, int]
    case_types: dict[str, int]


class DemoSeedResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    namespace: str
    created_document_count: int
    skipped_document_count: int
    uploaded_jobs: tuple[str, ...]
    status: Literal["ok"] = "ok"


class DemoNamespaceStore(Protocol):
    async def reset_namespace(self, namespace: str) -> None: ...

    async def document_exists(self, *, namespace: str, document_id: str) -> bool: ...

    async def record_document_seeded(self, *, namespace: str, document_id: str) -> None: ...


class DemoGovernanceStore(Protocol):
    async def upsert_tenant(self, *, tenant_id: str, display_name: str) -> None: ...

    async def upsert_role(
        self,
        *,
        tenant_id: str,
        role_id: str,
        permissions: tuple[str, ...],
    ) -> None: ...

    async def upsert_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        roles: tuple[str, ...],
        department: str | None,
        permissions: tuple[str, ...],
    ) -> None: ...

    async def assign_role(self, *, tenant_id: str, user_id: str, role_id: str) -> None: ...


class DemoUploadService(Protocol):
    async def upload(
        self,
        context: AuthenticatedRequestContext,
        command: UploadDocumentCommand,
    ) -> UploadDocumentResult: ...


class DemoSeedOrchestrator:
    def __init__(
        self,
        *,
        upload_service: DemoUploadService,
        namespace_store: DemoNamespaceStore,
        governance_store: DemoGovernanceStore | None = None,
    ) -> None:
        self._upload_service = upload_service
        self._namespace_store = namespace_store
        self._governance_store = governance_store

    async def seed(
        self,
        manifest: DemoManifest,
        *,
        context: AuthenticatedRequestContext,
        reset_demo_namespace: bool = False,
    ) -> DemoSeedResult:
        if reset_demo_namespace:
            await self._namespace_store.reset_namespace(manifest.namespace)

        await self._seed_governance(manifest)

        created = 0
        skipped = 0
        job_ids: list[str] = []
        for document in manifest.documents:
            if await self._namespace_store.document_exists(
                namespace=manifest.namespace,
                document_id=document.document_id,
            ):
                skipped += 1
                continue
            result = await self._upload_service.upload(
                context,
                _upload_command(manifest=manifest, document=document),
            )
            await self._namespace_store.record_document_seeded(
                namespace=manifest.namespace,
                document_id=document.document_id,
            )
            created += 1
            job_ids.append(result.job_id)

        return DemoSeedResult(
            namespace=manifest.namespace,
            created_document_count=created,
            skipped_document_count=skipped,
            uploaded_jobs=tuple(job_ids),
        )

    async def _seed_governance(self, manifest: DemoManifest) -> None:
        if self._governance_store is None:
            return
        tenant = manifest.tenants[0]
        await self._governance_store.upsert_tenant(
            tenant_id=tenant.tenant_id,
            display_name=tenant.display_name,
        )
        for role in manifest.roles:
            await self._governance_store.upsert_role(
                tenant_id=tenant.tenant_id,
                role_id=role.role_id,
                permissions=role.permissions,
            )
        for user in manifest.users:
            await self._governance_store.upsert_user(
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                roles=user.roles,
                department=user.department,
                permissions=user.permissions,
            )
            for role_id in user.roles:
                await self._governance_store.assign_role(
                    tenant_id=user.tenant_id,
                    user_id=user.user_id,
                    role_id=role_id,
                )


def load_demo_manifest(path: Path) -> DemoManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DemoSeedError(code="manifest_read_failed", details={"file": path.name}) from exc
    except json.JSONDecodeError as exc:
        raise DemoSeedError(
            code="manifest_invalid_json",
            details={"file": path.name, "line": exc.lineno, "column": exc.colno},
        ) from exc

    try:
        manifest = DemoManifest.model_validate(payload).model_copy(update={"manifest_path": path})
    except ValidationError as exc:
        raise DemoSeedError(
            code="unsafe_manifest",
            details={"file": path.name, "error_count": exc.error_count()},
        ) from exc

    _validate_corpus_files(manifest, path.parent)
    return manifest


def build_seed_plan(manifest: DemoManifest) -> DemoSeedPlan:
    categories: dict[str, int] = {}
    for document in manifest.documents:
        categories[document.category] = categories.get(document.category, 0) + 1
    case_types: dict[str, int] = {}
    for case in manifest.cases:
        case_types[case.case_type] = case_types.get(case.case_type, 0) + 1
    return DemoSeedPlan(
        namespace=manifest.namespace,
        tenant_count=len(manifest.tenants),
        user_count=len(manifest.users),
        role_count=len(manifest.roles),
        document_count=len(manifest.documents),
        case_count=len(manifest.cases),
        categories=categories,
        case_types=case_types,
    )


def write_seed_report(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = _sanitize_report_payload(payload)
    path.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def materialize_demo_corpus(manifest: DemoManifest, output_dir: Path) -> Path:
    if manifest.manifest_path is None:
        raise DemoSeedError(code="manifest_path_missing", details={"namespace": manifest.namespace})
    source_root = manifest.manifest_path.parent
    target_root = output_dir / manifest.namespace
    target_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest.manifest_path, target_root / "manifest.json")
    for document in manifest.documents:
        source = source_root / document.path
        target = target_root / document.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    write_seed_report(
        target_root / "seed-report.json",
        {
            "namespace": manifest.namespace,
            "status": "materialized",
            "plan": build_seed_plan(manifest).model_dump(mode="json"),
            "next_steps": [
                "python -m packages.data.demo_seed validate "
                "--manifest docs/demo/enterprise-rag/manifest.json"
            ],
        },
    )
    return target_root


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or materialize the synthetic RAG demo.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--manifest", required=True)
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--manifest", required=True)
    materialize_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    manifest = load_demo_manifest(Path(args.manifest))
    if args.command == "validate":
        print(json.dumps(build_seed_plan(manifest).model_dump(mode="json"), ensure_ascii=False))
        return 0
    if args.command == "materialize":
        target = materialize_demo_corpus(manifest, Path(args.output))
        print(json.dumps({"status": "materialized", "path": str(target)}, ensure_ascii=False))
        return 0
    return 2


def _upload_command(*, manifest: DemoManifest, document: DemoDocument) -> UploadDocumentCommand:
    if manifest.manifest_path is None:
        raise DemoSeedError(code="manifest_path_missing", details={"namespace": manifest.namespace})
    path = manifest.manifest_path.parent / document.path
    content = path.read_bytes()
    return UploadDocumentCommand(
        document_id=document.document_id,
        filename=Path(document.path).name,
        content_type="text/markdown" if document.source_type == "markdown" else "text/plain",
        source_type=document.source_type,
        source_uri=document.source_uri,
        title=document.title,
        acl=document.acl.model_dump(mode="json"),
        metadata={
            "demo_namespace": manifest.namespace,
            "demo_category": document.category,
            "source_display_name": document.title,
        },
        stream=BytesIO(content),
    )


def _validate_corpus_files(manifest: DemoManifest, root: Path) -> None:
    for document in manifest.documents:
        path = root / document.path
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DemoSeedError(
                code="corpus_read_failed",
                details={"document_id": document.document_id, "file": Path(document.path).name},
            ) from exc
        try:
            _safe_text(content, field_name="corpus", max_length=12000)
        except ValueError as exc:
            raise DemoSeedError(
                code="unsafe_manifest",
                details={"document_id": document.document_id, "field": "corpus"},
            ) from exc


def _text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("value must be a sequence of strings")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("values must be strings")
        text = _safe_text(item, field_name="list item", max_length=120)
        if text:
            normalized.append(text)
    return tuple(normalized)


def _safe_id(value: str) -> str:
    normalized = value.strip()
    if not SAFE_DEMO_ID_PATTERN.fullmatch(normalized):
        raise ValueError("identifier must use safe demo characters")
    _reject_forbidden_markers(normalized, "identifier")
    return normalized


def _safe_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    _reject_forbidden_markers(normalized, field_name)
    if _looks_like_path_or_token_url(normalized):
        raise ValueError(f"{field_name} must not include unsafe locators")
    return normalized


def _reject_forbidden_markers(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS):
        raise ValueError(f"{field_name} must not contain secret-like markers")


def _looks_like_path_or_token_url(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("file://")
        or WINDOWS_ABSOLUTE_PATH_PATTERN.search(value) is not None
        or re.search(r"(^|\s)/(etc|home|users|var|tmp)/", lowered) is not None
        or "?" in value
        and any(marker in lowered for marker in ("token=", "signature=", "x-amz-", "x-minio-"))
    )


def _sanitize_report_payload(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = str(key)
            if normalized_key.lower() in REPORT_FORBIDDEN_KEYS:
                continue
            sanitized = _sanitize_report_payload(item)
            if sanitized is not None:
                result[normalized_key] = sanitized
        return result
    if isinstance(value, list | tuple):
        return [_sanitize_report_payload(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS):
            return None
        if _looks_like_path_or_token_url(value):
            return None
        return value
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
