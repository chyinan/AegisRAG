---
baseline_commit: NO_VCS
---

# Story 2.2: Parser 协议与 Markdown/TXT 解析

Status: done

生成时间：2026-06-04T16:04:53+08:00

## Story

As a 知识库管理员,
I want Markdown 和 TXT 文档被标准化为统一解析结构,
so that 后续 chunking 和 indexing 不关心原始格式差异。

## Acceptance Criteria

1. **Markdown 标题层级解析为统一结构**
   - Given Markdown 文档包含多级标题
   - When Markdown parser 解析文档
   - Then 输出 `ParsedDocument`、`Section` 和标题层级
   - And 每个 section 保留 `title_path` 和 source metadata

2. **TXT 无标题文档解析为默认 section**
   - Given TXT 文档无显式标题
   - When TXT parser 解析文档
   - Then 输出至少一个默认 section
   - And parser 不丢失 `source_uri`、`tenant_id`、`document_id`、`version_id`

3. **Parser 错误进入领域异常和 job 状态**
   - Given parser 遇到非法编码或空文件
   - When ingestion worker 捕获错误
   - Then 错误被转换为领域异常
   - And job 状态更新为 `failed_retryable` 或 `failed_terminal`，包含 `error_code`

## Tasks / Subtasks

- [x] 新增 ingestion 领域模型与 parser 协议（AC: 1, 2, 3）
  - [x] 创建 `packages/ingestion/domain.py`，定义 `RawDocumentRef`、`ParsedDocument`、`Section` 等 Pydantic v2 DTO 或 dataclass，字段至少包含 `tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`title_path`、`content`、可选 `page_start/page_end`、`metadata`。
  - [x] 创建 `packages/ingestion/ports.py`，定义 `DocumentParser` Protocol：`async def parse(self, request: ParseRequest) -> ParsedDocument` 或等价接口。
  - [x] 创建 `packages/ingestion/exceptions.py`，定义稳定领域错误：`DOCUMENT_PARSE_UNSUPPORTED_TYPE`、`DOCUMENT_PARSE_EMPTY_CONTENT`、`DOCUMENT_PARSE_ENCODING_FAILED`、`DOCUMENT_PARSE_FAILED`。
  - [x] Parser DTO 不得依赖 FastAPI、SQLAlchemy、Redis、MinIO 或外部 SDK；storage model 不得传入 parser。

- [x] 实现 Markdown parser（AC: 1）
  - [x] 创建 `packages/ingestion/parsers/markdown.py`。
  - [x] 支持 ATX heading（`#` 到 `######`）生成层级 `title_path`；正文归入最近标题 section；文档开头无标题内容归入默认 section。
  - [x] 保留 `tenant_id`、`document_id`、`version_id`、`source_type=markdown`、`source_uri`、`acl` 和上传 metadata 摘要。
  - [x] 不渲染 HTML，不执行 Markdown 中任何内容；文档内容只作为不可信文本处理。
  - [x] MVP 可优先使用轻量行解析满足标题层级需求；未引入 `markdown-it-py` 或新增依赖。

- [x] 实现 TXT parser（AC: 2）
  - [x] 创建 `packages/ingestion/parsers/txt.py`。
  - [x] 对无显式标题 TXT 生成至少一个默认 section，使用基于 filename 的安全标题。
  - [x] 保留换行和段落边界，不做 cleaner、dedup、chunker 逻辑。
  - [x] 空白文件必须返回 `DOCUMENT_PARSE_EMPTY_CONTENT`，不能生成空 section 假装成功。

- [x] 实现 parser registry / selection（AC: 1, 2, 3）
  - [x] 创建 `packages/ingestion/service.py` 和 `packages/ingestion/parsers/registry.py`，按 `source_type` 选择 parser。
  - [x] 支持 `markdown`、`md`、`txt`；不支持类型返回 `DOCUMENT_PARSE_UNSUPPORTED_TYPE`。
  - [x] 不在本 story 实现 PDF/DOCX parser，但接口允许 Story 2.3 直接扩展。

- [x] 推进 ingestion worker 从 payload stub 到解析编排（AC: 3）
  - [x] 扩展 `apps/worker/jobs/ingestion_jobs.py`，继续先校验现有 `QueuePayload` ID-only contract。
  - [x] 新增 application/service 层编排：根据 `tenant_id`、`document_id`、`version_id` 查询 `DocumentVersionRecord`，从 `ObjectStorage` 读取 raw object，调用 parser。
  - [x] 扩展 `ObjectStorage` port，增加只读方法 `get_document(...) -> StoredDocumentContent`；真实 MinIO adapter 与 test fake storage 已实现。
  - [x] 解析成功时将 job 状态推进到 `parsed`，并保存 parsed metadata 摘要；文档说明后续 chunker 从 raw object 重新经 parser service/registry materialize `ParsedDocument`。
  - [x] 解析失败时更新 `ingestion_jobs.status` 为 `failed_terminal`（非法编码、空文件、不支持类型）或 `failed_retryable`（对象存储临时读取失败等），并记录 `error_code`、`attempt_count`、`last_attempt_at`。

- [x] 扩展 storage/repository 支撑 parser job 状态（AC: 3）
  - [x] 在 `packages/data/storage/repositories.py` 增加 tenant-scoped `get_ingestion_job()`、`mark_ingestion_job_parsing()`、`mark_ingestion_job_parsed()`、`mark_ingestion_job_failed()` 的必要字段更新。
  - [x] 未新增 parsed artifact 表或字段；复用 `document_versions.metadata` 保存安全摘要，未修改历史 migration。
  - [x] 所有 repository 方法返回 DTO，不把 SQLAlchemy model 泄漏到 ingestion/domain。

- [x] 补充审计、日志和安全边界（AC: 3）
  - [x] 解析开始、解析成功、解析失败都记录结构化日志和 audit，字段包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`document_id`、`version_id`、`job_id`、`source_type`、`latency_ms`、`status`、`error_code`。
  - [x] 日志和 audit 只能记录内容长度、section_count、title_path 摘要、checksum、错误码；不得记录完整文档正文、对象存储 secret、本机绝对路径或企业敏感全文。
  - [x] 文档中出现“忽略系统提示”“泄露密钥”等文字时，只作为普通文档内容保留，不触发工具、prompt 或权限逻辑。

- [x] 补充测试与验证（AC: 1, 2, 3）
  - [x] 单元测试：Markdown 多级标题、标题跳级、开头无标题正文、空 section 合并或保留策略、source metadata 贯穿。
  - [x] 单元测试：TXT 默认 section、换行保留、空文件、非法 UTF-8 或不允许编码。
  - [x] 单元测试：parser registry 对 `markdown/md/txt` 的选择和 unsupported type 错误。
  - [x] worker/service 测试：成功解析后 job 进入 `parsed`；parser 错误进入 `failed_terminal`；临时 object storage 读取失败进入 `failed_retryable`。
  - [x] 安全测试：queue payload 仍只含 IDs；parser 不执行文档内容；日志/audit 不含文档全文。
  - [x] 运行 `uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`；`uv run ruff check .` 通过，`uv run pytest` 和 `uv run mypy ...` 受 uv trampoline 影响失败；已用 `uv run python -m pytest` 和 `uv run python -m mypy apps packages tests` 验证通过。未新增 migration。

- [x] 更新必要文档（AC: 1, 2, 3）
  - [x] 更新 `docs/api/upload.md`，说明 Markdown/TXT parser 输出结构、错误码和 job 状态变化。
  - [x] 更新 `docs/operations/local-development.md`，说明如何用上传后的 job 触发或观察 parser 阶段。
  - [x] 未新增 dependency，无需更新 README/pyproject 依赖说明。

### Review Findings

- [x] [Review][Patch] Unsupported parser type can be masked by object-storage failure [packages/ingestion/service.py:177]
- [x] [Review][Patch] Unexpected parser/runtime failures leave jobs stuck in `parsing` [packages/ingestion/service.py:198]
- [x] [Review][Patch] Parse failures update only `ingestion_jobs`, not document/version status [packages/data/storage/repositories.py:67]
- [x] [Review][Patch] Parser job processing has no status guard or idempotency boundary [packages/ingestion/service.py:141]
- [x] [Review][Patch] Worker loses original `trace_id` by replacing it with `request_id` [apps/worker/jobs/ingestion_jobs.py:61]
- [x] [Review][Patch] MinIO read/delete methods trust `object_key` without tenant/document/version prefix validation [packages/data/adapters/minio_object_storage.py:145]
- [x] [Review][Patch] Parser stage does not verify object bytes against recorded checksum or byte size [packages/ingestion/service.py:177]
- [x] [Review][Patch] TXT parser strips leading/trailing whitespace instead of preserving text boundaries [packages/ingestion/parsers/txt.py:14]
- [x] [Review][Patch] Heading-only Markdown falls back to `Untitled` and loses title hierarchy [packages/ingestion/parsers/markdown.py:62]
- [x] [Review][Patch] Markdown parser treats heading-like lines inside fenced code blocks as real headings [packages/ingestion/parsers/markdown.py:46]
- [x] [Review][Patch] Job/version mismatch before parsing is not recorded as a job failure or audit event [packages/ingestion/service.py:141]
- [x] [Review][Patch] Parser service does not validate returned `ParsedDocument` IDs before marking parsed [packages/ingestion/service.py:184]
- [x] [Review][Defer] PDF/DOCX uploads are accepted before PDF/DOCX parsers exist [packages/data/service.py:47] — deferred, Story 2.3 owns PDF/DOCX parser support
- [x] [Review][Defer] Enqueued jobs store `queue_job_id` but never move to an explicit `queued` status [packages/data/storage/repositories.py:56] — deferred, queue lifecycle normalization spans Story 2.1/worker operations
- [x] [Review][Defer] Upload object cleanup can mask the original metadata write failure [packages/data/service.py:245] — deferred, upload compensation hardening belongs to upload service follow-up
- [x] [Review][Defer] Normal document listing does not exclude soft-deleted documents [packages/data/storage/repositories.py:143] — deferred, soft-delete query policy is a later document lifecycle concern
- [x] [Review][Defer] Restricted ACL can be accepted without any principals [packages/data/service.py:541] — deferred, full ACL semantics belong to RBAC/retrieval policy work

## Dev Notes

### Current Repository State

- `packages/ingestion` 当前不存在；本 story 应新增该 package，而不是把 parser 逻辑放入 `packages/data`、FastAPI route 或 worker job 函数内部。
- `packages/data` 已在 Story 2.1 承担文档上传、document/version/job storage、ObjectStorage/JobQueue ports 和 upload service；本 story 应复用这些治理数据，不重新设计上传 API。
- `apps/worker/jobs/ingestion_jobs.py` 当前只验证 `QueuePayload` 并返回 accepted stub；本 story 可以扩展 worker 编排，但必须继续保持 ID-only payload。
- `packages/data/ports.py` 的 `ObjectStorage` 目前只有 `put_document`、`delete_document`；解析需要新增 read port，而不是让 worker 直接 import MinIO SDK。
- `packages/data/storage/repositories.py` 已有 tenant-scoped `get_version()` 和 job failed/queued 更新；parser 阶段要扩展这些方法，保持 tenant filter 和 DTO 返回。
- `migrations/versions/20260527_0002_document_upload.py` 是已存在上传治理 migration；如果需要 parsed artifact 持久化，新增 revision，不重写历史 migration。
- 当前仓库没有 `.git`，无法从 commit 历史推断额外模式；以前一条 story 的 Dev Notes、Review Findings 和当前文件扫描为准。

### Architecture Requirements

- 本 story 属于 Domain Layer、Application Service Layer、Infrastructure Layer 和 Worker 编排，不新增 API endpoint。
- Parser 协议属于 `packages/ingestion/ports.py`；Markdown/TXT parser 属于 `packages/ingestion/parsers/*`。
- `RawDocument -> ParsedDocument -> Section` 是本 story 的边界；`Chunk`、cleaner、dedup、embedding、VectorStore、retrieval、RAG、citation extraction 不在本 story 实现。
- Domain 层不能 import FastAPI、SQLAlchemy、Redis、MinIO、httpx 或外部模型 SDK。
- Worker 只接收 `QueuePayload` 原始 dict 并校验，随后调用 application/service；不要把完整文档内容、file handle、AuthContext、SQLAlchemy model 或任意 Python object 放入队列。
- 解析结果必须保留 `tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`acl`、`checksum` 和 title metadata，后续 chunker 依赖这些字段。
- Parser 错误必须是领域异常，并最终写入 `ingestion_jobs.error_code`；不能只抛裸 `UnicodeDecodeError`、`ValueError` 或吞异常。

### Current Files To Preserve And Extend

- `apps/worker/jobs/ingestion_jobs.py`
  - Current state: 校验 `QueuePayload`、`job_type` 和 `{document_id, version_id}` 参数。
  - Story change: 保留 payload 校验，新增或委托 parser application service。
  - Preserve: ID-only payload、安全错误、无文件内容入队。

- `packages/data/ports.py`
  - Current state: `ObjectStorage` 支持 put/delete；`DocumentRepository` 支持 create/get version/job 状态部分更新。
  - Story change: 增加 raw object read port 和 parser job 所需 repository 方法。
  - Preserve: ports 是 Protocol，上层不依赖 MinIO/RQ/SQLAlchemy 具体类。

- `packages/data/storage/repositories.py`
  - Current state: SQLAlchemy repository 返回 Pydantic DTO，tenant-scoped 查询，storage 错误映射为 `StorageError`。
  - Story change: 增加 parsing/parsed/failed 状态更新和必要 artifact 查询/写入。
  - Preserve: 不泄漏 model，不跨 tenant 查询，不跳过 rollback/commit 边界。

- `packages/data/adapters/minio_object_storage.py`
  - Current state: 真实 object storage adapter 写入 raw document。
  - Story change: 增加读取 raw document 方法，设置 timeout，错误转领域/storage 错误。
  - Preserve: 不记录 endpoint secret、access key、secret key、bucket 内部敏感路径或文档全文。

- `packages/data/dto.py`
  - Current state: upload/document/version/job DTO。
  - Story change: 可保持数据治理 DTO；parser DTO 优先放入 `packages/ingestion/domain.py`，避免 data package 变成 ingestion domain。
  - Preserve: Pydantic v2、类型标注、字段边界。

### Previous Story Intelligence

- Story 2.1 的 review 已修复 queue 发布早于 DB job 持久化、object storage 补偿、failure audit commit、route 直接 wiring、ACL schema、content-type、empty file、worker payload 校验等问题；本 story 不得回退这些边界。
- 上传成功后 DB 是 job 状态真相；Redis/RQ 只是执行队列。Parser 阶段必须先读取并更新 DB job 状态，不能只返回 worker 结果。
- 权限和 tenant 边界已经从 upload metadata 开始贯穿；parser 必须传递这些 metadata，后续 chunk/retrieval 才能做 ACL filter。
- 前序测试命令基线为 `uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`；本 story 新增测试后应保持这些命令通过。

### Suggested Contracts

Parser protocol:

```python
from typing import Protocol


class DocumentParser(Protocol):
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        ...
```

Core DTO sketch:

```python
class ParseRequest(BaseModel):
    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    filename: str
    content: bytes
    acl: dict[str, object]
    metadata: dict[str, object] = {}


class Section(BaseModel):
    section_id: str
    title: str | None = None
    title_path: list[str]
    content: str
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict[str, object] = {}


class ParsedDocument(BaseModel):
    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    sections: list[Section]
    metadata: dict[str, object] = {}
```

Job status mapping:

| Condition | Status | error_code |
| --- | --- | --- |
| Worker begins parser stage | `parsing` | null |
| Markdown/TXT parse success | `parsed` | null |
| Empty content | `failed_terminal` | `DOCUMENT_PARSE_EMPTY_CONTENT` |
| Illegal encoding | `failed_terminal` | `DOCUMENT_PARSE_ENCODING_FAILED` |
| Unsupported parser type | `failed_terminal` | `DOCUMENT_PARSE_UNSUPPORTED_TYPE` |
| Temporary object storage read failure | `failed_retryable` | storage/read error code |

### Implementation Boundaries

- 不要实现 PDF/DOCX parser；Story 2.3 负责页码 metadata。
- 不要实现 cleaner、dedup、FixedSizeChunker、SemanticChunker、HierarchicalChunker、embedding provider、VectorStore、retrieval、RAG、citation 或 Agent。
- 不要新增 `/parse` API；解析由 ingestion worker/job 驱动。
- 不要在 parser 中执行 Markdown HTML、脚本、链接请求、frontmatter 指令或文档内自然语言命令。
- 不要把权限逻辑放进 prompt 或 parser 文本；权限来自 metadata/ACL 并由后续 retrieval 策略执行。
- 不要为了解析 TXT/Markdown 引入重型文档处理框架；如果依赖不足以证明必要，先用标准库和明确测试。
- 不要在 audit/log/error response 中记录完整文档内容。

### Latest Technical Information

- 当前 `pyproject.toml` 使用 Pydantic `>=2.13.4,<3`，PyPI 显示 Pydantic `2.13.4` 于 2026-05-06 发布；本 story 应继续使用 Pydantic v2 模式，不引入 v1 namespace。[Source: https://pypi.org/pypi/pydantic]
- `markdown-it-py` 在 PyPI release history 中显示 `4.2.0` 于 2026-05-07 发布；如果实现者需要 CommonMark token 级解析，可考虑 `markdown-it-py>=4.2.0,<5`，但 MVP 标题层级解析不一定需要新增依赖。[Source: https://pypi.org/project/markdown-it-py/]
- Python 官方 `codecs` 文档说明默认错误处理是 strict，解码错误会抛出异常；parser 应使用显式 UTF-8 strict 解码并把 `UnicodeDecodeError` 转为 `DOCUMENT_PARSE_ENCODING_FAILED`。[Source: https://docs.python.org/3.11/library/codecs.html]

### UX / Product Notes

- 本 story 不实现前端，但 Knowledge Admin 的 job status row 依赖稳定状态：`uploaded -> parsing -> parsed -> chunking -> chunked -> embedding -> indexing -> retrieval_ready`。
- 解析失败给管理员展示安全摘要：阶段、错误码、last_attempt_at、request_id/job_id；不要展示原始文档全文。
- 对普通查询用户，不暴露未授权或未完成索引文档的存在性；该安全边界主要在后续 retrieval，但 parser metadata 不能丢失 tenant/ACL。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 2.2`
- `_bmad-output/planning-artifacts/epics.md#Additional Requirements`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-2`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-21`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-23`
- `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration Points`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Flow 2`
- `_bmad-output/implementation-artifacts/2-1-授权文档上传与异步-ingestion-job.md`
- `apps/worker/jobs/ingestion_jobs.py`
- `packages/data/ports.py`
- `packages/data/storage/repositories.py`
- `packages/data/storage/models.py`
- `packages/data/adapters/minio_object_storage.py`
- `https://pypi.org/pypi/pydantic`
- `https://pypi.org/project/markdown-it-py/`
- `https://docs.python.org/3.11/library/codecs.html`

## Validation Checklist

Validation Result: PASS（2026-06-04T16:04:53+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 完整覆盖 Epic Story 2.2 的 Markdown 标题层级、TXT 默认 section、非法编码/空文件错误和 job 状态。
- [x] Tasks 覆盖 ingestion domain、parser protocol、Markdown/TXT parser、registry、worker orchestration、storage/job 状态、audit/log、测试和文档。
- [x] Dev Notes 明确复用 Story 2.1 的 document/version/job/object storage/queue 基础，不重复上传 API 或绕过 ID-only queue。
- [x] 明确禁止 PDF/DOCX、cleaner、dedup、chunker、embedding、retrieval、RAG、Agent 等越界实现。
- [x] 明确要求 parser 不执行文档内容，不记录完整正文，错误必须转领域异常并写入 job 状态。
- [x] 包含当前代码文件状态、前一条 story 经验和最新技术参考。

## Change Log

- 2026-06-04: Created comprehensive Story 2.2 developer context for parser protocol, Markdown/TXT parsing, ingestion worker parser orchestration, job state transitions, tests and implementation boundaries.
- 2026-06-04: Implemented Story 2.2 parser protocol, Markdown/TXT parsers, registry, parser service, worker orchestration, repository status updates, tests, and docs.
- 2026-06-04: Applied code review fixes for parser failure handling, trace propagation, object storage scoping, object integrity checks, parser edge cases, and status synchronization.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `uv run pytest` failed before pytest startup: `uv trampoline failed to canonicalize script path`; verified with `uv run python -m pytest`.
- `uv run mypy apps packages tests` failed before mypy startup: `uv trampoline failed to canonicalize script path`; verified with `uv run python -m mypy apps packages tests`.
- `uv run ruff check .` passed.

### Completion Notes List

- Added `packages/ingestion` domain DTOs, parser protocol, stable parse exceptions, Markdown/TXT parser adapters, and parser registry without FastAPI/SQLAlchemy/MinIO dependencies in parser DTOs.
- Implemented ingestion parser service that tenant-scopes job/version reads, reads raw object content through `ObjectStorage.get_document()`, maps parse/storage failures to `failed_terminal` or `failed_retryable`, records safe audit/log metadata, and stores parsed artifact summary in `document_versions.metadata`.
- Updated worker ingestion job to validate ID-only queue payload and run parser orchestration through injected service or default worker service wiring.
- Added unit and integration tests for parser behavior, registry selection, worker delegation, parser service status mapping, safe audit/log summaries, and repository parser job state transitions.
- Updated upload/local development documentation for parser output structure, job states, error codes, and the no-full-ParsedDocument-persistence boundary.

### File List

- apps/worker/jobs/ingestion_jobs.py
- docs/api/upload.md
- docs/operations/local-development.md
- packages/data/adapters/minio_object_storage.py
- packages/data/dto.py
- packages/data/exceptions.py
- packages/data/ports.py
- packages/data/service.py
- packages/data/storage/repositories.py
- packages/ingestion/__init__.py
- packages/ingestion/domain.py
- packages/ingestion/exceptions.py
- packages/ingestion/ports.py
- packages/ingestion/service.py
- packages/ingestion/parsers/__init__.py
- packages/ingestion/parsers/_common.py
- packages/ingestion/parsers/markdown.py
- packages/ingestion/parsers/registry.py
- packages/ingestion/parsers/txt.py
- tests/integration/storage/test_document_repositories.py
- tests/unit/data/test_ingestion_queue_payload.py
- tests/unit/ingestion/test_parse_service.py
- tests/unit/ingestion/test_parsers.py
