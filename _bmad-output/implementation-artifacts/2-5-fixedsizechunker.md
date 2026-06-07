---
baseline_commit: NO_VCS
---

# Story 2.5: FixedSizeChunker

Status: done

生成时间：2026-06-06T15:14:19+08:00

## Story

As a 知识库管理员,
I want 清洗后的文档按固定大小切成可检索 chunk,
so that MVP 有稳定、可测试的默认 chunk 策略。

## Acceptance Criteria

1. **默认固定大小 chunk 稳定、可配置**
   - Given 文档进入 `FixedSizeChunker`
   - When 使用默认策略切分
   - Then `chunk.token_count` 默认落在 500 到 800 token 目标范围内
   - And overlap 支持 10% 到 20% 配置

2. **chunk 保留治理、citation 和 section 来源信息**
   - Given 文档包含标题层级和页码
   - When chunker 切分跨 section 内容
   - Then chunk 保留 `title_path`、`page_start`、`page_end` 和原始 section 关联
   - And 单测覆盖 overlap、标题路径、页码和 `token_count`

3. **异常和降级不会产生不合规 chunk**
   - Given token 估算器不可用或文本异常
   - When chunker 执行
   - Then 返回领域错误或安全降级的 `token_count`
   - And 不产生缺少治理字段的 chunk

## Tasks / Subtasks

- [x] 定义 chunker 领域合同和 DTO（AC: 1, 2, 3）
  - [x] 在 `packages/ingestion/domain.py` 新增不可变 Pydantic v2 `Chunk` DTO，继续保持 domain 层不依赖 FastAPI、SQLAlchemy、Redis、MinIO、LLM、embedding 或 vector store。
  - [x] `Chunk` 必须至少包含 `chunk_id`、`tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`title_path`、`content`、`page_start`、`page_end`、`token_count`、`acl`、`checksum`、`section_ids`、`metadata`。
  - [x] `section_ids` 必须记录该 chunk 覆盖的原始 `Section.section_id`，用于后续 citation/source inspector；不得只保存拼接后的正文。
  - [x] 在 `packages/ingestion/ports.py` 增加 `Chunker` Protocol：`split(document: ParsedDocument) -> list[Chunk]`。
  - [x] 新增 `FixedSizeChunkerConfig` 或等价配置 DTO，默认目标范围为 `min_tokens=500`、`max_tokens=800`，默认 overlap 在 10% 到 20% 区间内，例如 `overlap_ratio=0.15`。

- [x] 实现 `FixedSizeChunker`（AC: 1, 2, 3）
  - [x] 新增 `packages/ingestion/chunkers/__init__.py` 和 `packages/ingestion/chunkers/fixed_size.py`。
  - [x] 输入必须是 parser/cleaner/dedup 后的 `ParsedDocument`；组件本身不读取 object storage、不调用 parser、不访问数据库、不入队、不调用 embedding/LLM/vector store。
  - [x] 按 section 原始顺序切分，保留段落边界优先；不要重新排序 section，不要跨 tenant/document/version 边界处理。
  - [x] 默认策略优先生成 500 到 800 token 的 chunk；短文档允许生成低于 500 token 的单个 chunk，但必须记录实际 `token_count`。
  - [x] overlap 使用 token 预算近似控制，默认 15%，配置只接受 0.10 到 0.20；越界配置必须抛出稳定领域异常，不能静默修正。
  - [x] 跨 section 合并时，`title_path` 使用最具体且可追溯的策略：单 section chunk 保留该 section `title_path`；多 section chunk 可使用共同前缀或第一个 section 的 `title_path`，但 `metadata` 必须记录覆盖的 title paths 摘要。
  - [x] `page_start` 取覆盖 sections 中最小非空页码，`page_end` 取最大非空页码；全部页码为空时保持 `None`，不得为 DOCX/TXT/Markdown 伪造页码。
  - [x] `acl` 必须与 document/section 保持一致；如果覆盖 sections 的 ACL 不一致，必须抛出稳定领域异常或拆分 chunk，不能合并成权限更宽的 chunk。
  - [x] `chunk_id` 必须稳定、可测试。建议使用 deterministic input（tenant/document/version、section_ids、chunk index、content checksum）生成 UUIDv5 或稳定 SHA-256 派生 ID；不要使用随机 UUID 作为默认 chunk_id。
  - [x] `checksum` 使用 canonical chunk content 的 UTF-8 SHA-256；可复用 `packages.ingestion.cleaner.stable_content_checksum`，不要使用 Python 内置 `hash()`。

- [x] 实现 token 估算与异常边界（AC: 1, 3）
  - [x] 不新增第三方 tokenizer 依赖，除非同时更新依赖、测试和文档并证明必要；当前 MVP 可实现标准库 deterministic estimator。
  - [x] 建议新增 `TokenEstimator` Protocol 或内部可注入 callable，默认 estimator 使用保守规则估算中英文混合文本 token 数，保证 deterministic tests。
  - [x] token estimator 返回非正数、抛出预期异常或遇到异常文本时，必须转为领域错误或安全降级；不得生成 `token_count <= 0` 的 chunk。
  - [x] 新增稳定错误码，例如 `DOCUMENT_CHUNK_CONFIG_INVALID`、`DOCUMENT_CHUNK_FAILED` 或 `DOCUMENT_CHUNK_EMPTY_CONTENT`；不要复用 parser/cleaner 错误码伪装 chunker 失败。
  - [x] 异常 details 只包含安全摘要，例如 `document_id`、`version_id`、`section_id`、`reason`，不得包含正文片段、prompt、token、密钥或本机绝对路径。

- [x] 补充测试（AC: 1, 2, 3）
  - [x] 新增 `tests/unit/ingestion/test_fixed_size_chunker.py`。
  - [x] 覆盖长文档默认切分：chunk `token_count` 大多位于 500 到 800，最后一个尾块可低于 500，但不得为空。
  - [x] 覆盖 10% 和 20% overlap 配置，以及默认 15% overlap；断言相邻 chunk 有可预期的重叠内容或 token overlap 摘要。
  - [x] 覆盖跨 section 合并时保留 `section_ids`、`title_path`、页码范围、source metadata、ACL、tenant/document/version。
  - [x] 覆盖 DOCX/TXT/Markdown 页码为空时不伪造页码。
  - [x] 覆盖 ACL 不一致、空 cleaned document、非法 overlap、token estimator 失败或返回非法值。
  - [x] 所有测试使用 synthetic text；不得调用 embedding、LLM、vector store、MinIO、Redis、真实数据库或外部 API。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_fixed_size_chunker.py`、`.venv\Scripts\python.exe -m pytest tests/unit/ingestion`、`.venv\Scripts\python.exe -m pytest`、`.venv\Scripts\python.exe -m ruff check .`、`.venv\Scripts\python.exe -m mypy apps packages tests`。

- [x] 更新必要文档（AC: 1, 2, 3）
  - [x] 更新 `docs/api/upload.md` 的 ingestion pipeline：说明 `parse -> clean -> dedup -> chunk` 的 chunker 阶段和当前 API 不同步执行 chunking。
  - [x] 更新 `docs/operations/local-development.md`：加入 FixedSizeChunker 本地单测命令和安全 metadata 说明。
  - [x] 如 `README.md` 描述 ingestion 当前能力，同步补充 FixedSizeChunker 作为已实现纯组件；明确 chunk 持久化、embedding、vector indexing 仍由后续 stories 落地。

### Review Findings

- [x] [Review][Patch] Valid `max_tokens=1` config can hang `FixedSizeChunker.split()` [packages/ingestion/chunkers/fixed_size.py:70]
- [x] [Review][Patch] `FixedSizeChunker` destroys paragraph boundaries while rebuilding chunk content [packages/ingestion/chunkers/fixed_size.py:114]
- [x] [Review][Patch] Chunker does not validate section `source_type` and `source_uri` before copying document source metadata [packages/ingestion/chunkers/fixed_size.py:186]
- [x] [Review][Patch] Duplicate `section_id` values can collapse distinct section lineage and bypass ACL checks [packages/ingestion/domain.py:193]
- [x] [Review][Patch] Explicit `acl=None` becomes `{}` instead of tenant-default ACL [packages/ingestion/domain.py:162]
- [x] [Review][Patch] Invalid page ranges are accepted into citation metadata [packages/ingestion/domain.py:128]
- [x] [Review][Patch] Custom token estimator is not used for chunk boundaries and can exceed configured token budget [packages/ingestion/chunkers/fixed_size.py:93]
- [x] [Review][Patch] `min_tokens` is configured but tiny non-final/tail chunks are not actively controlled [packages/ingestion/chunkers/fixed_size.py:72]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 基于现有源码、Story 2.1 到 2.4 记录、epics、architecture 和项目规则生成。
- `packages/ingestion/domain.py` 已定义 frozen Pydantic v2 DTO：`RawDocumentRef`、`ParseRequest`、`Section`、`ParsedDocument`。本 story 应在同一文件新增 `Chunk`，不要创建与 ingestion domain 脱节的并行 DTO。
- `packages/ingestion/ports.py` 已有 `DocumentParser`、`DocumentCleaner`、`DocumentDeduplicator` Protocol，应在此处新增 `Chunker` Protocol。
- `packages/ingestion/cleaner.py` 提供 `DefaultDocumentCleaner`、`stable_content_checksum()`、`canonicalize_content()`；chunk checksum 可复用稳定 checksum 逻辑。
- `packages/ingestion/dedup.py` 提供 `ExactSectionDeduplicator`，会保留 source order，并在 section metadata 中维护 `content_checksum`。
- `packages/ingestion/chunkers/` 当前不存在，应新增目录和 `fixed_size.py`。不要把 chunker 放进 parser、cleaner 或 service 文件。
- `packages/ingestion/service.py` 当前只做 parser job：读取 raw object、parser registry、校验 parsed metadata、记录 safe parsed summary。不要在本 story 重写 parser job；如仅实现纯组件，文档需说明后续 chunker job/service 会串联 `parse -> clean -> dedup -> chunk`。
- 当前 Alembic migrations 尚未创建 `chunks` 表；Story 2.6 负责 chunk metadata contract 与持久化。本 story 只创建 in-memory domain DTO、协议、组件、测试和文档。

### Architecture Requirements

- 本 story 属于 `packages/ingestion`，位置是 `RawDocument -> ParsedDocument -> Section -> Chunk` 中的 `Section -> Chunk` 阶段。
- API route 只处理 HTTP contract；不得新增 `/chunk` API，不得让 FastAPI route 直接切正文或拼 metadata。
- Domain/Application 组件不得 import FastAPI、SQLAlchemy、Redis、MinIO、httpx、LLM SDK、embedding provider 或 vector store。
- Chunker 不做权限决策，但必须保留并校验 `tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`acl`，后续 retrieval/RBAC 依赖这些字段。
- 文档内容不可信；chunker 只做文本切分和 metadata 传递，不执行 HTML、链接、宏、脚本、外部资源或文档内 prompt-like 指令。
- 日志、异常、metadata 只允许安全摘要；不得写入完整 chunk 正文、被删除正文、企业机密片段、prompt、token、API key 或本机绝对路径。

### Current Files To Preserve And Extend

- `packages/ingestion/domain.py`
  - Current state: frozen Pydantic v2 DTO，校验 required IDs、`Section.content` 非空、`ParsedDocument.sections` 非空。
  - Story change: 新增 `Chunk` DTO 和必要校验。
  - Preserve: `ParsedDocument.checksum` 继续表示 raw object/version checksum；不要改成 chunk checksum。

- `packages/ingestion/ports.py`
  - Current state: parser/cleaner/dedup Protocol。
  - Story change: 增加 `Chunker` Protocol。
  - Preserve: Protocol 不依赖具体实现、不引入 I/O。

- `packages/ingestion/exceptions.py`
  - Current state: parser 和 cleaner 相关稳定错误码。
  - Story change: 增加 chunker 相关稳定错误码和异常。
  - Preserve: 不回退 parser/cleaner 错误码语义。

- `packages/ingestion/chunkers/fixed_size.py`（NEW）
  - Implement: `FixedSizeChunkerConfig`、token estimator、`FixedSizeChunker`。
  - Preserve: source order、section lineage、page ranges、ACL 和治理字段。

- `packages/ingestion/chunkers/__init__.py`（NEW）
  - Implement: 导出 fixed size chunker 的公共类型。
  - Preserve: 不在 import 时执行外部 I/O。

- `tests/unit/ingestion/test_fixed_size_chunker.py`（NEW）
  - Implement: focused unit tests with synthetic text only。
  - Preserve: 不复用真实企业文档、不触碰外部依赖。

- `docs/api/upload.md`、`docs/operations/local-development.md`、`README.md`
  - Current state: 已说明 parser、cleaner、dedup 和 parse job 仍只记录 parsed safe summary。
  - Story change: 增加 chunker 纯组件和验证命令。
  - Preserve: 不宣称 chunk 持久化、embedding 或 vector indexing 已完成。

### Previous Story Intelligence

- Story 2.1 建立授权上传、ObjectStorage port、DocumentRepository、RQ queue、ID-only payload、audit/error envelope；本 story 不应修改上传权限或重新设计 job 创建。
- Story 2.2 建立 parser DTO、registry、Markdown/TXT parser、worker parser service 和 job 状态推进；本 story 应复用 `ParsedDocument` / `Section`，不重新发明 parser pipeline。
- Story 2.3 完成 PDF/DOCX parser，并修复 parser job claim、payload mismatch、section 逐段校验、安全摘要、parser 异常映射和 service failure tests；Story 2.5 不能让这些安全边界回退。
- Story 2.4 已实现 cleaner/dedup，并明确保守边界：不接入 parse service、不存完整 cleaned document、只记录安全摘要；Story 2.5 应继续保持纯组件优先，等待后续 job/service 串联。
- Story 2.4 明确 PDF 页码为 1-based，DOCX 页码为 `None`；chunker 必须继承这个事实，不得给 DOCX 伪造页码。
- 前序验证发现 `uv run pytest` 和 `uv run mypy ...` 在当前 Windows 环境可能受 uv trampoline 影响；优先使用 `.venv\Scripts\python.exe -m pytest` / `mypy` 或 `uv run python -m pytest`。

### Suggested Contracts

Protocol shape:

```python
from typing import Protocol

from packages.ingestion.domain import Chunk, ParsedDocument


class Chunker(Protocol):
    def split(self, document: ParsedDocument) -> list[Chunk]:
        ...
```

Chunk DTO shape:

```python
class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    title_path: list[str]
    content: str
    page_start: int | None = None
    page_end: int | None = None
    token_count: int
    acl: dict[str, object] = Field(default_factory=_default_acl)
    checksum: str
    section_ids: list[str]
    metadata: dict[str, object] = Field(default_factory=dict)
```

Do not add SQLAlchemy fields or persistence behavior to this DTO; Story 2.6 owns storage mapping.

### Implementation Boundaries

- 不要实现 SemanticChunker、HierarchicalChunker、embedding-aware chunking、LLM chunking、table-aware chunking 或 OCR/layout chunking。
- 不要引入 LangChain text splitter；当前项目要求核心 ingestion 逻辑保持自研、可测试、不过度绑定框架。
- 不要同步改 `/upload` 为等待 chunking 完成；上传接口仍立即返回 job/version/status。
- 不要创建 `chunks` 表、pgvector index、embedding job 或 retrieval API；这些属于 Story 2.6 到 3.x。
- 不要跨 tenant、document 或 version 合并内容。
- 不要为了满足 500 token 下限而把无关 section 强行合并到权限或 metadata 不一致的 chunk。
- 不要将 chunk content 写入日志、audit、document_versions.metadata 或错误 details。
- 不要把 token 估算做成模型厂商相关逻辑；后续 LLM/RAG 可替换更精确 tokenizer，但本 story 的默认实现必须 deterministic、离线、可测试。

### Latest Technical Information

- Python 3.11 标准库 `hashlib.sha256()` 适合生成跨进程稳定 checksum；不要使用内置 `hash()` 作为持久或可测试标识。[Source: https://docs.python.org/3.11/library/hashlib.html]
- Python 3.11 标准库 `uuid.uuid5(namespace, name)` 可基于 namespace/name 生成确定性 UUID，适合可复现 chunk_id；如使用 UUIDv5，namespace 必须固定且代码中有清晰命名。[Source: https://docs.python.org/3.11/library/uuid.html]
- Pydantic v2 `BaseModel.model_copy(update=...)` 可用于从 frozen DTO 派生副本；新增 chunker 不应原地修改 `ParsedDocument` 或 `Section`。[Source: https://docs.pydantic.dev/latest/api/base_model/]

### UX / Product Notes

- 本 story 不实现前端，但 Knowledge Admin 后续 job/progress UI 会依赖安全摘要：chunk count、token count range、overlap ratio、error_code。
- 管理端不得展示完整 chunk 正文作为日志或状态摘要；需要排查时使用 request_id、job_id、document_id、version_id、chunk_id、section_ids 和安全计数。
- FixedSizeChunker 质量直接影响 citation 和 retrieval：必须保留 PDF 1-based page ranges、DOCX 空页码和 section lineage。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 2.5`
- `_bmad-output/planning-artifacts/epics.md#Epic 2`
- `_bmad-output/planning-artifacts/architecture.md#Architectural Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping`
- `_bmad-output/implementation-artifacts/2-4-cleaner-与-dedup.md`
- `project-context.md`
- `packages/ingestion/domain.py`
- `packages/ingestion/ports.py`
- `packages/ingestion/cleaner.py`
- `packages/ingestion/dedup.py`
- `packages/ingestion/service.py`
- `tests/unit/ingestion/test_cleaner.py`
- `tests/unit/ingestion/test_dedup.py`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `README.md`
- `https://docs.python.org/3.11/library/hashlib.html`
- `https://docs.python.org/3.11/library/uuid.html`
- `https://docs.pydantic.dev/latest/api/base_model/`

## Validation Checklist

Validation Result: PASS（2026-06-06T15:14:19+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 完整覆盖 Epic Story 2.5 的默认 token 范围、overlap、metadata 保留、异常降级和测试要求。
- [x] Tasks 覆盖 DTO、Protocol、FixedSizeChunker、token estimator、领域异常、测试和文档。
- [x] Dev Notes 明确复用现有 `ParsedDocument` / `Section` / cleaner / dedup，不重新发明 parser pipeline。
- [x] 明确 `ParsedDocument.checksum` 继续表示 raw object/version checksum，chunk checksum 独立生成。
- [x] 明确禁止 semantic/hierarchical/LLM/vector chunking、chunk 持久化、embedding、retrieval 和 route 直接切正文。
- [x] 包含当前代码文件状态、前序 story 经验、最新 Python/Pydantic 技术参考和实现边界。

## Change Log

- 2026-06-06: Created comprehensive Story 2.5 developer context for FixedSizeChunker, Chunk DTO, Chunker Protocol, token estimation, metadata preservation, tests, docs and implementation boundaries.
- 2026-06-06: Implemented FixedSizeChunker, Chunk DTO, Chunker Protocol, chunker errors, unit tests, documentation updates, and completed validation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_fixed_size_chunker.py` -> 14 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/ingestion` -> 56 passed
- `.venv\Scripts\python.exe -m pytest` -> 214 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed

### Completion Notes List

- Added immutable `Chunk` DTO and `Chunker` Protocol while keeping ingestion domain free of API, storage, queue, LLM, embedding, and vector-store dependencies.
- Added `FixedSizeChunker` with deterministic token estimation, configurable 10% to 20% overlap, stable UUIDv5 chunk IDs, SHA-256 content checksums, section lineage, title path summaries, page ranges, ACL consistency checks, and safe chunker error details.
- Added focused synthetic unit coverage for default chunk size, overlap behavior, metadata preservation, page handling, ACL mismatch, empty content, invalid config, and token estimator failure/fallback.
- Updated upload/local development/README documentation to describe `parse -> clean -> dedup -> chunk` and clarify that chunk persistence, embedding, and vector indexing remain future work.

### File List

- `packages/ingestion/domain.py`
- `packages/ingestion/ports.py`
- `packages/ingestion/exceptions.py`
- `packages/ingestion/chunkers/__init__.py`
- `packages/ingestion/chunkers/fixed_size.py`
- `tests/unit/ingestion/test_fixed_size_chunker.py`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `README.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-5-fixedsizechunker.md`
