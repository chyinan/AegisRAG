---
baseline_commit: NO_VCS
---

# Story 2.4: Cleaner 与 Dedup

Status: done

生成时间：2026-06-04T21:00:41+08:00

## Story

As a 知识库管理员,
I want 解析后的文档先被清洗和去重,
so that 后续 chunking 不会把页眉页脚、重复 section 或噪声内容写入索引。

## Acceptance Criteria

1. **清洗和去重输出稳定、可测试**
   - Given `ParsedDocument` 包含重复空白、页眉页脚或重复 section
   - When cleaner 和 dedup 执行
   - Then 输出稳定、可测试的清洗结果
   - And checksum 能用于识别重复内容

2. **清洗后不破坏治理和 citation metadata**
   - Given 清洗过程删除或合并内容
   - When 输出 cleaned document
   - Then 保留 `document_id`、`version_id`、`tenant_id`、`source_uri`、`title_path` 和页码范围
   - And 不丢失后续 chunk metadata 所需字段

3. **单测覆盖正常、边界和异常场景**
   - Given cleaner 或 dedup 单测运行
   - When 输入重复段落、空白、页眉页脚和空文档
   - Then 覆盖正常、边界和异常场景
   - And 不调用 embedding、LLM 或 vector store

## Tasks / Subtasks

- [x] 定义 cleaner/dedup 合同、错误码和稳定 checksum 策略（AC: 1, 2）
  - [x] 在 `packages/ingestion/ports.py` 增加纯 Python Protocol：`DocumentCleaner.clean(document: ParsedDocument) -> ParsedDocument` 与 `DocumentDeduplicator.deduplicate(document: ParsedDocument) -> ParsedDocument`。
  - [x] 不新增并行 parser DTO；输入输出继续使用现有 `ParsedDocument` / `Section`，通过 Pydantic v2 `model_copy(update=...)` 生成不可变对象副本。
  - [x] 保留顶层 `ParsedDocument.checksum` 为 raw object checksum，不能改成 cleaned checksum，否则会破坏 `IngestionParseService._ensure_parsed_matches_request()` 的版本一致性校验。
  - [x] 为清洗/去重新增稳定 metadata checksum：建议每个 section 写入 `metadata["content_checksum"] = sha256(canonical_content)`，document metadata 写入 `cleaned_section_count`、`removed_section_count`、`deduped_section_count`、`cleaning_stage="cleaned"`。
  - [x] checksum 输入必须是规范化后的文本 bytes，使用 UTF-8 编码和 `hashlib.sha256`；不要使用 Python 内置 `hash()`，因为它不是跨进程稳定值。

- [x] 实现 deterministic cleaner（AC: 1, 2）
  - [x] 新增 `packages/ingestion/cleaner.py`，实现 `DefaultDocumentCleaner` 或同等命名，保持无外部 I/O、无数据库、无 ObjectStorage、无 LLM。
  - [x] 对 section content 做保守清洗：统一 CRLF/LF、去除行尾空白、压缩连续空行、可选 Unicode normalization（NFKC 需有测试覆盖），保留段落边界。
  - [x] 页眉页脚移除必须保守：只删除跨多个 PDF 页重复出现的短行，且该行在足够多的 page section 中重复；单页文档、DOCX 无页码文档、正文中偶然重复的句子不得被误删。
  - [x] 清洗后如果 section content 为空，删除该 section，并在 document metadata 中记录安全摘要，例如 `removed_empty_section_count`；不得把被删除正文写入 metadata/log/audit。
  - [x] 清洗后的 section 必须保留原 `section_id`、`tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`title`、`title_path`、`page_start`、`page_end`、`acl`。
  - [x] 如果整个 document 清洗后没有 section，抛出稳定领域异常；在 `packages/ingestion/exceptions.py` 新增 `DOCUMENT_CLEAN_EMPTY_CONTENT` 和 `DocumentCleanError`/`EmptyCleanedDocumentError`，不能返回空 `ParsedDocument`。

- [x] 实现 exact dedup（AC: 1, 2）
  - [x] 新增 `packages/ingestion/dedup.py`，实现 `ExactSectionDeduplicator` 或同等命名，MVP 仅做确定性 exact/canonical dedup，不做 semantic/fuzzy dedup。
  - [x] canonical key 使用 `content_checksum + normalized title_path`，默认只删除同标题路径下的 exact duplicate section，保留第一次出现的 section，删除后续重复 section。
  - [x] 保留 source order，不重新排序 section；后续 chunker/citation 依赖 parser 输出顺序和页码顺序。
  - [x] dedup 后的 metadata 只记录安全摘要：`duplicate_section_count`、`kept_section_ids` 可选、`dropped_duplicate_section_ids` 可选；不得记录重复正文。
  - [x] 不要跨 tenant、跨 document 或跨 version dedup；本 story 只处理单个 `ParsedDocument` 内部重复，跨版本/跨文档索引治理留给后续版本治理或 indexing story。

- [x] 补充 application/service 接入边界（AC: 1, 2）
  - [x] 本 story 不新增 `/clean`、`/dedup`、`/parse` API，也不让 FastAPI route 直接调用 cleaner/dedup。
  - [x] 不改 queue payload contract；payload 仍只包含 ID 和 JSON 可序列化参数。
  - [x] 如更新 `IngestionParseService`，只能增加可选、默认启用的 cleaner/dedup 组合步骤，并必须保持 job claim、payload mismatch、section 逐段校验、安全 audit/log 行为不回退。
  - [x] 更保守方案是只实现可复用组件和单测，等 Story 2.5/2.6 chunker 按 `parse -> clean -> dedup -> chunk` 串联；若选择该方案，需在 docs 明确当前 parser job 仍只记录 `parsed` 安全摘要。
  - [x] 无论是否接入 service，清洗/去重组件都必须独立可测试，且不依赖数据库状态。

- [x] 补充测试（AC: 1, 2, 3）
  - [x] 新增 `tests/unit/ingestion/test_cleaner.py`：覆盖空白归一化、连续空行压缩、保留段落边界、保留 governance fields、清洗后空文档错误。
  - [x] 新增 `tests/unit/ingestion/test_dedup.py`：覆盖重复 section 删除、保留第一个 section、保留页码/title_path/ACL、metadata checksum 稳定、source order 不变。
  - [x] PDF 页眉页脚测试需使用多页 `Section(page_start/page_end)` synthetic input；断言重复页眉/页脚被删，正文重复句子不被误删。
  - [x] Markdown/TXT/DOCX 输入测试需断言没有页码时不会执行页眉页脚误删。
  - [x] 所有测试使用 synthetic text，不提交企业真实文档，不调用 embedding、LLM、vector store、MinIO、Redis 或外部 API。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/unit/ingestion`、`.venv\Scripts\python.exe -m pytest`、`.venv\Scripts\python.exe -m ruff check .`、`.venv\Scripts\python.exe -m mypy apps packages tests`。

- [x] 更新必要文档（AC: 1, 2）
  - [x] 更新 `docs/api/upload.md` 的 Parser Stage 或新增 ingestion pipeline 小节，说明 normalized cleanup 是 parser 后、chunker 前的纯后端步骤。
  - [x] 更新 `docs/operations/local-development.md`：说明如何通过单测验证 cleaner/dedup，且当前阶段不展示或记录被删除正文。
  - [x] 如 README 描述 ingestion 当前能力，同步补充 cleaner/dedup 作为已实现组件或后续 chunker 串联前置步骤。

### Review Findings

- [x] [Review][Patch] PDF cleaner can delete repeated body lines, not just headers/footers [packages/ingestion/cleaner.py:115]
- [x] [Review][Patch] Dedup trusts existing `metadata.content_checksum` instead of verifying content [packages/ingestion/dedup.py:52]
- [x] [Review][Patch] Dedup can merge duplicate content across different ACLs [packages/ingestion/dedup.py:16]
- [x] [Review][Patch] NFKC normalization is enabled without required Unicode test coverage [tests/unit/ingestion/test_cleaner.py:53]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，无法读取 commit 历史；本 story 基于 Story 2.1、2.2、2.3、规划文档和当前源码扫描生成。
- `packages/ingestion/cleaner.py` 和 `packages/ingestion/dedup.py` 当前不存在，应新增为 ingestion 包内组件。
- `packages/ingestion/domain.py` 已定义不可变 Pydantic v2 DTO：`ParseRequest`、`ParsedDocument`、`Section`。不要新增重复的 `CleanedSection` DTO，除非能证明后续 Story 2.5/2.6 需要独立类型。
- `Section.content` 不允许为空，`ParsedDocument.sections` 不允许为空；清洗后空内容必须转为领域异常，而不是构造非法 DTO。
- `packages/ingestion/ports.py` 当前只有 `DocumentParser` Protocol，本 story 应在同一文件补 cleaner/dedup Protocol，保持分层边界。
- `packages/ingestion/service.py` 当前完成 parser registry 调用、object checksum 校验、parsed ID 校验、section 逐段治理字段校验、job 状态、audit/log 和安全 summary。不要重写这条链路。
- `docs/api/upload.md` 当前明确 parser stage 不持久化完整 `ParsedDocument`，后续 chunker stage 应重新物化 `ParsedDocument`；Story 2.4 若不接入 service，需要保持这个事实一致。

### Architecture Requirements

- 本 story 属于 `packages/ingestion`，位置在 `RawDocument -> ParsedDocument -> Section -> Chunk` 的 `ParsedDocument/Section` 后处理阶段。
- API route 只处理 HTTP contract；不得在 route 中清洗正文、去重、拼接 metadata 或调用 parser/cleaner/dedup。
- Domain/Application 组件不得 import FastAPI、SQLAlchemy、Redis、MinIO、httpx、LLM SDK、embedding provider 或 vector store。
- cleaner/dedup 不做权限决策，但必须原样传递 `tenant_id`、`document_id`、`version_id`、`source_uri`、`acl`，后续 retrieval/RBAC 依赖这些字段。
- 文档内容不可信；清洗器只按文本规则处理，不执行 HTML、链接、宏、脚本、外部资源或 prompt-like 指令。
- 日志、audit、document metadata 只允许安全摘要，不允许写入原文正文、被删除正文、企业机密片段、prompt 文本、token 或本机绝对路径。

### Current Files To Preserve And Extend

- `packages/ingestion/domain.py`
  - Current state: Pydantic v2 frozen DTO，`Section` 和 `ParsedDocument` 校验治理字段、content 和 section 非空。
  - Story change: 一般无需修改；如新增 helper 必须保持 DTO 向后兼容。
  - Preserve: `ParsedDocument.checksum` 与 raw object/version checksum 一致。

- `packages/ingestion/ports.py`
  - Current state: 只有 `DocumentParser` Protocol。
  - Story change: 增加 `DocumentCleaner`、`DocumentDeduplicator` Protocol。
  - Preserve: Protocol 不依赖具体实现、不引入 I/O。

- `packages/ingestion/exceptions.py`
  - Current state: 只有 parser 相关错误码和异常。
  - Story change: 增加 cleaner 相关稳定错误码和异常，至少覆盖清洗后空文档。
  - Preserve: parser 错误码语义不变；不要把 cleaner 错误伪装成 encoding/parser unsupported。

- `packages/ingestion/cleaner.py`（NEW）
  - Implement: deterministic whitespace/header/footer/empty-section cleaning。
  - Preserve: section order、metadata、page ranges、ACL 和治理字段。

- `packages/ingestion/dedup.py`（NEW）
  - Implement: exact section dedup with stable content checksum。
  - Preserve: first occurrence、source order、tenant/document/version boundaries。

- `packages/ingestion/service.py`
  - Current state: parser service 已有 job claim、payload mismatch 拒绝、parser failure mapping、safe summary。
  - Story change: 仅在决定接入 clean/dedup 到 parse service 时小范围注入组件；否则不改。
  - Preserve: 不回退 Story 2.3 review 修复，尤其是 payload mismatch 不应把真实 job 标失败。

- `tests/unit/ingestion/test_parse_service.py`
  - Current state: fake repository/storage/parser 模式完善，可用于服务层接入测试。
  - Story change: 如果服务层接入 cleaner/dedup，补成功和失败状态测试。
  - Preserve: fake-only，不触碰真实 Redis/MinIO/DB。

- `tests/unit/ingestion/test_parsers.py`
  - Current state: Markdown/TXT/PDF/DOCX parser 已覆盖 parser 行为。
  - Story change: 不应把 cleaner/dedup 断言塞进 parser 测试；新增专门 cleaner/dedup 测试文件更清晰。

### Previous Story Intelligence

- Story 2.1 建立授权上传、ObjectStorage port、DocumentRepository、RQ queue、ID-only payload、audit/error envelope；本 story 不应修改上传权限或重新设计 job 创建。
- Story 2.2 建立 parser DTO、registry、Markdown/TXT parser、worker parser service 和 job 状态推进；本 story 应复用这些 DTO，不重新发明 parser pipeline。
- Story 2.3 完成 PDF/DOCX parser，并修复 parser job claim、payload mismatch、section 逐段校验、安全摘要、parser 异常映射和 service failure tests；Story 2.4 不能让这些安全边界回退。
- Story 2.3 明确 PDF 页码为 1-based，DOCX 页码为 `None`；页眉页脚清洗必须只依赖可靠页码，不得给 DOCX 伪造页码。
- 前序验证发现 `uv run pytest` 和 `uv run mypy ...` 在当前 Windows 环境可能受 uv trampoline 影响；优先使用 `.venv\Scripts\python.exe -m pytest` / `mypy` 或 `uv run python -m pytest`。

### Suggested Contracts

Protocol shape:

```python
from typing import Protocol

from packages.ingestion.domain import ParsedDocument


class DocumentCleaner(Protocol):
    def clean(self, document: ParsedDocument) -> ParsedDocument:
        ...


class DocumentDeduplicator(Protocol):
    def deduplicate(self, document: ParsedDocument) -> ParsedDocument:
        ...
```

Section checksum guidance:

```python
canonical = "\n".join(line.rstrip() for line in content.splitlines()).strip()
content_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
cleaned_section = section.model_copy(
    update={
        "content": canonical,
        "metadata": {**section.metadata, "content_checksum": content_checksum},
    }
)
```

Do not use this to update `ParsedDocument.checksum`; that field remains the raw upload/version checksum.

### Implementation Boundaries

- 不要实现 semantic dedup、embedding-based dedup、LLM 判断重复、跨文档/跨版本 dedup。
- 不要引入新第三方库；标准库 `hashlib`、`re`、`unicodedata` 足够。
- 不要删除没有可靠证据的重复正文；页眉页脚规则宁可少删，不可误删业务内容。
- 不要把 `title_path`、页码或 ACL 作为 prompt 文本处理；它们是结构化 metadata。
- 不要把 cleaner/dedup 失败吞掉后继续 chunk；空文档或非法结果必须显式失败。
- 不要把完整 cleaned document 存入 `document_versions.metadata`、audit log 或普通日志。
- 不要在本 story 实现 chunker、embedding、VectorStore、retrieval、RAG、citation extractor 或 Open WebUI UI。

### Latest Technical Information

- Python 3.11 标准库 `hashlib` 保证提供 `sha256()`，适合生成跨进程稳定的内容 checksum；不要使用内置 `hash()` 做持久或可测试标识。[Source: https://docs.python.org/3.11/library/hashlib.html]
- Python 3.11 标准库 `unicodedata.normalize(form, text)` 可做 Unicode normalization；若启用 NFKC，需要测试中文、英文、全角/半角和标点边界，避免改变业务含义。[Source: https://docs.python.org/3.11/library/unicodedata.html]
- Pydantic v2 `BaseModel.model_copy(update=...)` 可用于从 frozen DTO 派生副本；本 story 应使用现有 DTO 的副本更新，而不是原地修改。[Source: https://docs.pydantic.dev/latest/api/base_model/]

### UX / Product Notes

- 本 story 不实现前端，但 Knowledge Admin 后续 job/progress UI 会依赖安全摘要：section count、removed count、duplicate count、error_code。
- 管理端不得展示被删除页眉页脚或重复正文全文；需要排查时只显示 job_id/request_id/error_code 和安全计数。
- cleaner/dedup 质量直接影响 citation：必须保留 PDF 1-based page ranges 和 DOCX `title_path`，否则后续 Source Inspector 无法可靠定位。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 2.4`
- `_bmad-output/planning-artifacts/epics.md#Epic 2`
- `_bmad-output/planning-artifacts/architecture.md#Architectural Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping`
- `_bmad-output/implementation-artifacts/2-1-授权文档上传与异步-ingestion-job.md`
- `_bmad-output/implementation-artifacts/2-2-parser-协议与-markdown-txt-解析.md`
- `_bmad-output/implementation-artifacts/2-3-pdf-docx-parser-与页码-metadata.md`
- `project-context.md`
- `packages/ingestion/domain.py`
- `packages/ingestion/ports.py`
- `packages/ingestion/service.py`
- `packages/ingestion/parsers/_common.py`
- `tests/unit/ingestion/test_parse_service.py`
- `tests/unit/ingestion/test_parsers.py`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `https://docs.python.org/3.11/library/hashlib.html`
- `https://docs.python.org/3.11/library/unicodedata.html`
- `https://docs.pydantic.dev/latest/api/base_model/`

## Validation Checklist

Validation Result: PASS（2026-06-04T21:00:41+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 完整覆盖 Epic Story 2.4 的清洗、去重、checksum、metadata 保留和测试要求。
- [x] Tasks 覆盖接口合同、cleaner、dedup、服务接入边界、测试和文档。
- [x] Dev Notes 明确复用现有 `ParsedDocument` / `Section`，不重新发明 parser DTO。
- [x] 明确顶层 `ParsedDocument.checksum` 必须继续表示 raw object/version checksum，避免破坏 parser service 校验。
- [x] 明确禁止 semantic/LLM/vector dedup、跨文档 dedup、正文入日志/审计、API route 直接处理正文。
- [x] 包含当前代码文件状态、前序 story 经验、最新标准库/Pydantic 技术参考和实现边界。

## Change Log

- 2026-06-04: Implemented deterministic cleaner, exact dedup, stable content checksums, tests, docs, and validation for Story 2.4.
- 2026-06-04: Created comprehensive Story 2.4 developer context for deterministic cleaner, exact dedup, stable content checksum, metadata preservation, tests, docs and implementation boundaries.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- Red phase: `.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_cleaner.py tests/unit/ingestion/test_dedup.py` failed during collection because `packages.ingestion.cleaner` did not exist.
- Green validation: `.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_cleaner.py tests/unit/ingestion/test_dedup.py` passed with 11 tests.
- Regression validation: `.venv\Scripts\python.exe -m pytest tests/unit/ingestion` passed with 42 tests.
- Full validation: `.venv\Scripts\python.exe -m pytest` passed with 200 tests.
- Quality validation: `.venv\Scripts\python.exe -m ruff check .` passed.
- Type validation: `.venv\Scripts\python.exe -m mypy apps packages tests` passed after explicit metadata value type narrowing in cleaner tests.

### Completion Notes List

- Implemented `DocumentCleaner` and `DocumentDeduplicator` Protocol contracts without adding parallel parser DTOs or external I/O dependencies.
- Added deterministic `DefaultDocumentCleaner` using canonical UTF-8 SHA-256 content checksums, conservative PDF repeated short-line header/footer removal, zero-width noise removal, empty-section dropping, safe metadata counters, and stable `DOCUMENT_CLEAN_EMPTY_CONTENT` domain failure when all content is removed.
- Added `ExactSectionDeduplicator` for same-document exact duplicate removal keyed by `content_checksum + normalized title_path`, preserving first occurrence and source order.
- Chose the conservative service boundary: parse job behavior and queue payload contract are unchanged; docs state that Story 2.5/2.6 should run `parse -> clean -> dedup -> chunk`.
- Added focused unit tests with synthetic text only; no embedding, LLM, vector store, MinIO, Redis, database, or external API calls.

### File List

- `README.md`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `packages/ingestion/cleaner.py`
- `packages/ingestion/dedup.py`
- `packages/ingestion/exceptions.py`
- `packages/ingestion/ports.py`
- `tests/unit/ingestion/test_cleaner.py`
- `tests/unit/ingestion/test_dedup.py`
- `_bmad-output/implementation-artifacts/2-4-cleaner-与-dedup.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
