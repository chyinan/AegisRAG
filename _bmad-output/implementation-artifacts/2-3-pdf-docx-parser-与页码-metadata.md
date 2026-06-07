---
baseline_commit: NO_VCS
---

# Story 2.3: PDF/DOCX Parser 与页码 Metadata

Status: done

生成时间：2026-06-04T17:09:47+08:00

## Story

As a 知识库管理员,
I want PDF 和 DOCX 文档被解析并尽量保留页码或结构信息,
so that 后续 citation 能追溯到原文位置。

## Acceptance Criteria

1. **PDF 多页文本解析保留页码范围**
   - Given PDF 文档包含多页文本
   - When PDF parser 解析文档
   - Then section 或 block metadata 包含 `page_start` 和 `page_end`
   - And 解析结果可被 chunker 消费

2. **DOCX 标题和段落解析保留标题层级**
   - Given DOCX 文档包含标题和段落
   - When DOCX parser 解析文档
   - Then 输出保留标题层级的 `Section`
   - And 没有页码时必须显式设置页码为空而不是伪造页码

3. **PDF/DOCX parser 失败可追踪且不泄露正文**
   - Given PDF 或 DOCX parser 失败
   - When worker 更新 job
   - Then 失败原因可从 job 状态中查询
   - And 日志只记录错误摘要，不记录企业机密全文

## Tasks / Subtasks

- [x] 增加 PDF/DOCX 解析依赖和错误边界（AC: 1, 2, 3）
  - [x] 在 `pyproject.toml` 增加 `pypdf>=6.12.2,<7` 和 `python-docx>=1.2.0,<2`，并更新 `uv.lock`。
  - [x] 依赖只允许被 `packages/ingestion/parsers/pdf.py` 和 `packages/ingestion/parsers/docx.py` 等 infrastructure/parser adapter 使用；domain DTO、API route、storage model 不得直接依赖这些库。
  - [x] 在 `packages/ingestion/exceptions.py` 中复用或补充稳定 parser 错误码；损坏文件、加密/不可读 PDF、无可提取文本、非法 DOCX 包等必须转为 `DocumentParseError`，不能泄漏原始异常类型给 API/worker 外层。
  - [x] 不在本 story 引入 OCR、表格结构抽取、图片文字识别或版面重建；扫描件 PDF 允许返回明确 parser 失败或空文本错误。

- [x] 实现 PDF parser（AC: 1, 3）
  - [x] 新增 `packages/ingestion/parsers/pdf.py`，实现 `DocumentParser` Protocol：`async def parse(self, request: ParseRequest) -> ParsedDocument`。
  - [x] 使用 `pypdf.PdfReader` 从 `bytes`/`BytesIO` 读取，不读取本机路径，不把 object key 当成本地文件路径。
  - [x] 按页提取文本，每个有非空文本的页面至少生成一个 `Section`，`page_start` 和 `page_end` 均为 1-based 页码。
  - [x] section 必须保留 `tenant_id`、`document_id`、`version_id`、`source_type="pdf"`、`source_uri`、`acl`、`checksum`、`title_path` 和安全 metadata。
  - [x] `title_path` MVP 可使用 `[safe_title_from_filename(filename), "Page {n}"]`；如读取 PDF outline/bookmark，只能作为 metadata 或额外 title hints，不能因 outline 缺失而失败。
  - [x] 对空 PDF、全扫描件/无可提取文本、损坏 PDF、加密且无法读取 PDF 做确定性错误映射，并写入 job `failed_terminal` 或 `failed_retryable`。
  - [x] 提取过程中不得记录页面全文；日志/audit 只记录 `page_count`、`section_count`、页码范围、checksum、error_code。

- [x] 实现 DOCX parser（AC: 2, 3）
  - [x] 新增 `packages/ingestion/parsers/docx.py`，使用 `python-docx` 从 `BytesIO` 读取 `.docx`。
  - [x] 通过 paragraph style 的英文内建名称识别 `Title`、`Heading 1` 到 `Heading 9`，维护标题栈并生成 `title_path`。
  - [x] 正文段落归入最近标题 section；文档开头无标题内容归入 `[safe_title_from_filename(filename)]` 默认 section。
  - [x] DOCX parser 不伪造页码：所有 `Section.page_start`、`Section.page_end` 必须为 `None`，metadata 可包含 `"page_metadata": "unavailable"` 或等价安全摘要。
  - [x] 空段落只作为边界处理，不生成空 section；只有图片、空段落或无法提取正文的 DOCX 返回 parser 空内容错误。
  - [x] 表格内容如果实现，只能作为后续增强；MVP 可明确只抽取段落文本，避免引入未经测试的表格序列化。

- [x] 扩展 parser registry 和 worker 现有链路（AC: 1, 2, 3）
  - [x] 更新 `packages/ingestion/parsers/registry.py`，让默认 registry 支持 `pdf`、`docx`，并保留现有 `markdown`、`md`、`txt`。
  - [x] 不新增 `/parse` API；继续由 `apps/worker/jobs/ingestion_jobs.py` 通过 `IngestionParseService` 驱动 parser。
  - [x] 不改 queue payload contract；payload 仍只包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`job_type`、`resource_id=job_id`、`parameters={document_id, version_id}`。
  - [x] 保持 `IngestionParseService` 的 object checksum/byte_size 校验、parsed document ID 校验、状态幂等和安全 audit/log 行为。
  - [x] 更新 parsed summary 时增加安全字段：PDF `page_count/page_ranges`，DOCX `heading_count` 或 `page_metadata=unavailable`；不得持久化完整正文。

- [x] 补充测试与 fixtures（AC: 1, 2, 3）
  - [x] 更新 `tests/unit/ingestion/test_parsers.py`：registry 对 `pdf/docx` 选择成功；未知类型仍返回 `DOCUMENT_PARSE_UNSUPPORTED_TYPE`。
  - [x] PDF parser 单测：两页文本生成两个或多个 section，页码为 1-based；空/无文本 PDF 返回稳定 parser 错误；损坏 PDF 返回稳定 parser 错误；metadata 不包含正文。
  - [x] DOCX parser 单测：`Title`、`Heading 1/2` 和正文生成正确 `title_path`；无标题 DOCX 使用 filename 默认 title；页码字段显式为 `None`；空 DOCX 返回稳定 parser 错误。
  - [x] `IngestionParseService` 测试：`source_type=pdf/docx` 不再在读取对象前报 unsupported；parser 成功进入 `parsed`；parser 失败进入正确失败状态并记录 error_code。
  - [x] 使用测试内生成的最小 PDF/DOCX bytes 或 `tests/fixtures/ingestion/` synthetic fixtures；禁止提交企业真实文档或敏感全文。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/unit/ingestion`、`.venv\Scripts\python.exe -m pytest`、`.venv\Scripts\python.exe -m ruff check .`、`.venv\Scripts\python.exe -m mypy apps packages tests`。

- [x] 更新必要文档（AC: 1, 2, 3）
  - [x] 更新 `docs/api/upload.md` 的 Parser Stage：supported parser source types 增加 `pdf`、`docx`，说明 PDF 页码和 DOCX 无页码策略。
  - [x] 更新 `docs/operations/local-development.md`：补充本地上传 PDF/DOCX 后如何观察 `parsing -> parsed`、失败错误码和安全摘要。
  - [x] 若 README 中列出支持格式，同步说明 PDF/DOCX parser 已通过 worker 链路支持，但 OCR、表格结构化和原文预览不属于本 story。

### Review Findings

- [x] [Review][Decision] Parser job claim/retry 语义不完整 — resolved with production claim model: repository-level conditional claim, stale `parsing` retry cutoff, and no job mutation for active claims or mismatched payloads.
- [x] [Review][Patch] 队列 payload ID 不匹配会把真实 job 标记失败 [packages/ingestion/service.py:152]
- [x] [Review][Patch] parsed section 未逐段校验 tenant/document/version/source/ACL 一致性 [packages/ingestion/service.py:511]
- [x] [Review][Patch] parsed summary、日志和审计写入原始 title_paths，DOCX 标题可能是敏感正文 [packages/ingestion/service.py:471]
- [x] [Review][Patch] PDF/DOCX 懒加载解析异常未完整映射为领域异常 [packages/ingestion/parsers/pdf.py:32]
- [x] [Review][Patch] 缺少 PDF/DOCX parser 失败经 IngestionParseService 写入失败状态和 error_code 的测试 [tests/unit/ingestion/test_parse_service.py:325]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，无法读取 commit 历史；本 story 基于 Story 2.1、Story 2.2、规划文档和当前源码扫描生成。
- `pyproject.toml` 当前没有 `pypdf` 或 `python-docx`，但上传 API 已允许 PDF/DOCX 文件；Story 2.2 的 review 已把“PDF/DOCX uploads are accepted before PDF/DOCX parsers exist”明确 defer 给本 story。
- `packages/ingestion/domain.py` 已定义 `ParseRequest`、`ParsedDocument`、`Section`，并支持可选 `page_start/page_end`；不要新增并行 DTO。
- `packages/ingestion/ports.py` 已定义 `DocumentParser` Protocol；PDF/DOCX parser 必须实现该协议。
- `packages/ingestion/service.py` 已负责 job/version 校验、object storage 读取、parser registry 调用、checksum/byte_size 校验、parsed ID 校验、job 状态推进、audit/log 和 parsed summary；本 story 应扩展 parser，不重写 service 编排。
- `packages/ingestion/parsers/registry.py` 当前只注册 `markdown/md/txt`；这是本 story 的主更新点之一。
- `tests/unit/ingestion/test_parsers.py` 当前断言 `registry.get("pdf")` 为 unsupported；实现本 story 时必须更新该断言。
- `docs/api/upload.md` 当前列出 supported parser source types 为 `markdown/md/txt`；实现本 story 后必须同步文档。

### Architecture Requirements

- 本 story 属于 `packages/ingestion` 的 parser adapter 能力和 worker ingestion 编排延伸，不新增 API endpoint，不实现 chunker、cleaner、dedup、embedding、VectorStore、retrieval、RAG 或 citation extractor。
- 解析结果必须继续服从 `RawDocument -> ParsedDocument -> Section`；`Chunk` 仍由后续 Story 2.5/2.6 负责。
- Domain 层不能 import FastAPI、SQLAlchemy、Redis、MinIO、httpx、LLM SDK 或外部模型 SDK。
- API route 不得调用 parser、pypdf 或 python-docx；route 继续只处理 upload contract 和 service 调用。
- Worker queue payload 必须保持 ID-only；PDF/DOCX bytes 只能来自 ObjectStorage，通过 `ObjectStorage.get_document()` 读取。
- `tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`acl`、`checksum` 必须从 `DocumentVersionRecord -> ParseRequest -> ParsedDocument -> Section` 贯穿。
- Parser 不做权限决策，不执行文档内容，不把 prompt injection 文本当作指令；权限和 ACL 只作为结构化 metadata 传递给后续 retrieval。

### Current Files To Preserve And Extend

- `packages/ingestion/parsers/registry.py`
  - Current state: 注册 `MarkdownParser`、`TxtParser`，`pdf` 会返回 unsupported。
  - Story change: 注册 `PdfParser` 和 `DocxParser`。
  - Preserve: unknown source type 仍返回 `UnsupportedDocumentTypeError`；source_type normalization 保持 strip/lower。

- `packages/ingestion/parsers/_common.py`
  - Current state: UTF-8 decoding helper 和 section metadata helper。
  - Story change: 可增加通用 metadata helper，但不要强迫 PDF/DOCX 走 UTF-8 decode。
  - Preserve: 空内容错误和安全 metadata 习惯，不记录正文。

- `packages/ingestion/service.py`
  - Current state: parser service 已处理状态、audit、checksum、ID 校验、失败映射。
  - Story change: 一般无需改；只有 parsed summary 需要增加页码/heading 安全摘要时才做小范围扩展。
  - Preserve: DB 是 job 状态真相；Redis/RQ 不是状态真相；日志不含正文。

- `tests/unit/ingestion/test_parsers.py`
  - Current state: Markdown/TXT parser 单测和 registry unsupported 测试。
  - Story change: 增加 PDF/DOCX parser 单测，并更新 registry 期望。
  - Preserve: 不调用真实外部 LLM/API；fixtures 必须 synthetic。

- `tests/unit/ingestion/test_parse_service.py`
  - Current state: parser service 成功、unsupported、storage failure、unexpected parser failure、idempotency、payload mismatch 测试。
  - Story change: 增加 `source_type=pdf/docx` 成功/失败映射测试，或通过 registry 单测覆盖 parser availability。
  - Preserve: fake storage/fake repository 模式。

- `docs/api/upload.md`
  - Current state: 上传 API 和 Markdown/TXT parser stage 说明。
  - Story change: 补充 PDF/DOCX 支持、页码策略、OCR/表格边界和错误码。
  - Preserve: 上传不等待 parser/chunk/embedding 完成；错误和 audit 不含正文。

### Previous Story Intelligence

- Story 2.1 已建立授权上传、ObjectStorage port、DocumentRepository、RQ queue、ID-only payload、audit/error envelope；本 story 不应修改上传权限或重新设计 job 创建。
- Story 2.2 已建立 parser DTO、registry、Markdown/TXT parser、worker parser service 和 job 状态推进；本 story 应直接复用这些接口。
- Story 2.2 review 修复了 unsupported type 被 storage failure 掩盖、parser unexpected failure 让 job 卡在 `parsing`、trace_id 丢失、object_key scope 校验、checksum 校验、parser result ID 校验等问题；PDF/DOCX parser 不得回退这些边界。
- 前序验证发现 `uv run pytest` 和 `uv run mypy ...` 在当前 Windows 环境可能受 uv trampoline 影响；可用 `.venv\Scripts\python.exe -m pytest` / `mypy` 或 `uv run python -m pytest` 验证。

### Suggested Contracts

PDF section shape:

```python
Section(
    section_id=f"{request.version_id}:page-{page_number}",
    tenant_id=request.tenant_id,
    document_id=request.document_id,
    version_id=request.version_id,
    source_type="pdf",
    source_uri=request.source_uri,
    title=f"Page {page_number}",
    title_path=[safe_title_from_filename(request.filename), f"Page {page_number}"],
    content=page_text,
    page_start=page_number,
    page_end=page_number,
    acl=request.acl,
    metadata={...safe metadata only...},
)
```

DOCX heading handling:

```text
Title       -> title_path root, page_start/page_end = None
Heading 1   -> level 1 title_path
Heading 2   -> level 2 title_path
Normal text -> current title_path section body
No heading  -> [safe_title_from_filename(filename)]
```

Failure mapping guidance:

| Condition | Status | error_code |
| --- | --- | --- |
| PDF/DOCX parser unsupported dependency or implementation bug | `failed_retryable` | `DOCUMENT_PARSE_FAILED` |
| Empty extracted content | `failed_terminal` | `DOCUMENT_PARSE_EMPTY_CONTENT` |
| Damaged/invalid PDF or DOCX package | `failed_terminal` or retryable by chosen exception | `DOCUMENT_PARSE_FAILED` |
| Encrypted/unreadable PDF | `failed_terminal` | `DOCUMENT_PARSE_FAILED` or more specific stable code if added |
| Object storage read/integrity mismatch | `failed_retryable` | `DOCUMENT_STORAGE_READ_FAILED` |

### Implementation Boundaries

- 不要实现 OCR；扫描件 PDF 如果无文本层，应返回安全失败或空内容错误。
- 不要实现复杂表格抽取、图片 caption 抽取、公式解析、PDF 坐标级 layout 重建或文档预览。
- 不要新增 `/parse`、`/documents/{id}/parse` 或任何同步解析 API。
- 不要在 parser 中执行 DOCX/PDF 内的宏、链接、JavaScript、外部资源、文档指令或 prompt-like 内容。
- 不要把完整 `ParsedDocument` 或原文正文写入 DB metadata、audit、log、error details 或 eval report。
- 不要把本机绝对路径、object storage secret、bucket 内部敏感路径或企业原文写入错误响应。
- 不要让 DOCX parser 猜测页码；没有可靠页码时必须为 `None`。
- 不要让 PDF 页码从 0 开始；citation 面向用户必须使用 1-based 页码。

### Latest Technical Information

- `pypdf` 当前 PyPI 最新包为 `6.12.2`，上传时间为 2026-05-26；本 story 推荐 `pypdf>=6.12.2,<7`。[Source: https://pypi.org/project/pypdf/]
- pypdf 6.12.2 文档显示可以通过 `PdfReader` 和 `page.extract_text()` 提取页面文本，也支持 `extraction_mode="layout"`；但文档同时警告解析大 content stream 可能显著消耗内存，应考虑先检查 `page.get_contents().get_data()` 大小。[Source: https://pypdf.readthedocs.io/en/stable/user/extract-text.html]
- pypdf 文档明确说明扫描件或图片型 PDF 可能只有极少或空文本，pypdf 不是 OCR 软件；本 story 不应承诺 OCR。[Source: https://pypdf.readthedocs.io/en/stable/user/extract-text.html]
- `python-docx` 当前 PyPI 版本为 `1.2.0`，上传时间为 2025-06-16，项目描述为读取、创建和更新 Microsoft Word 2007+ `.docx` 文件；本 story 推荐 `python-docx>=1.2.0,<2`。[Source: https://pypi.org/project/python-docx/]
- python-docx 1.2.0 文档说明 `Document()` 可从文件路径或 file-like object 加载 `.docx`，因此 parser 应使用 `BytesIO(request.content)` 而不是本机路径。[Source: https://python-docx.readthedocs.io/en/latest/api/document.html]
- python-docx 文档说明 Word 内建样式在 WordprocessingML 中使用英文名，例如 `Heading 1`，即使本地化 Word UI 显示本地语言；DOCX heading 识别必须基于英文 style name 或明确 fallback。[Source: https://python-docx.readthedocs.io/en/stable/user/styles-using.html]

### UX / Product Notes

- 本 story 不实现前端，但 Knowledge Admin job row 会依赖 parser 阶段的状态、错误码和安全摘要。
- PDF 成功解析后，后续 Source Inspector/citation 需要 `page_start/page_end`；本 story 必须保证页码从 parser 阶段进入 `Section`。
- DOCX 无页码是正常状态，后续 citation 可退化为 document/version/chunk/title_path；不得为了 UI 好看伪造页码。
- 管理端可展示 `parsed`、`failed_retryable`、`failed_terminal`、`page_count`、`section_count`、`error_code`、`request_id/job_id`；不得展示完整文档正文。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 2.3`
- `_bmad-output/planning-artifacts/epics.md#Story 2.2`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-2`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-16`
- `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration Points`
- `_bmad-output/implementation-artifacts/2-1-授权文档上传与异步-ingestion-job.md`
- `_bmad-output/implementation-artifacts/2-2-parser-协议与-markdown-txt-解析.md`
- `packages/ingestion/domain.py`
- `packages/ingestion/ports.py`
- `packages/ingestion/service.py`
- `packages/ingestion/parsers/registry.py`
- `tests/unit/ingestion/test_parsers.py`
- `tests/unit/ingestion/test_parse_service.py`
- `docs/api/upload.md`
- `https://pypi.org/project/pypdf/`
- `https://pypdf.readthedocs.io/en/stable/user/extract-text.html`
- `https://pypi.org/project/python-docx/`
- `https://python-docx.readthedocs.io/en/latest/api/document.html`
- `https://python-docx.readthedocs.io/en/stable/user/styles-using.html`

## Validation Checklist

Validation Result: PASS（2026-06-04T17:09:47+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 完整覆盖 Epic Story 2.3 的 PDF 页码 metadata、DOCX 标题层级、失败状态和安全日志。
- [x] Tasks 覆盖依赖、PDF parser、DOCX parser、registry/service 链路、测试和文档。
- [x] Dev Notes 明确复用 Story 2.2 的 parser protocol、registry、worker parse service 和 job 状态编排，不重复上传 API。
- [x] 明确要求 PDF 页码为 1-based、DOCX 页码为 `None`，禁止伪造 citation 页码。
- [x] 明确禁止 OCR、表格结构化、同步解析 API、正文入日志/审计和 parser 执行文档内容。
- [x] 包含当前代码文件状态、前序 story 经验、最新 pypdf/python-docx 技术参考和实现边界。

## Change Log

- 2026-06-04: Created comprehensive Story 2.3 developer context for PDF/DOCX parser support, page metadata, DOCX title hierarchy, parser registry extension, tests, docs and implementation boundaries.
- 2026-06-04: Implemented PDF/DOCX parser support through worker parser chain, safe summaries, tests, and documentation.
- 2026-06-04: Code review fixes completed for parser job claim semantics, payload mismatch handling, section-level validation, safe summaries, parser exception mapping, and PDF/DOCX service failure tests.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

- 2026-06-04T17:14:50+08:00: Story status moved to in-progress; baseline_commit preserved as NO_VCS.
- 2026-06-04T17:17:00+08:00: Parser tests first failed on missing `packages.ingestion.parsers.docx/pdf`, confirming red phase.
- 2026-06-04T17:19:00+08:00: Ingestion parser tests passed after implementing PDF/DOCX parser adapters and registry support.
- 2026-06-04T17:21:00+08:00: Full validation passed: ingestion tests, full pytest, ruff, and mypy.

### Completion Notes List

- Added `pypdf` and `python-docx` runtime dependencies while keeping imports isolated to parser adapter modules.
- Implemented `PdfParser` using `PdfReader(BytesIO(...))`, generating page sections with 1-based `page_start/page_end` and safe PDF summary metadata.
- Implemented `DocxParser` using `python-docx`, preserving `Title` and `Heading 1..9` hierarchy and explicitly leaving page metadata unavailable.
- Extended default parser registry for `pdf` and `docx`; `IngestionParseService` now records PDF/DOCX safe summary fields without persisting full text.
- Added synthetic PDF/DOCX unit coverage and updated upload/local development/README documentation.

### File List

- `pyproject.toml`
- `uv.lock`
- `packages/ingestion/parsers/pdf.py`
- `packages/ingestion/parsers/docx.py`
- `packages/ingestion/parsers/registry.py`
- `packages/ingestion/service.py`
- `tests/unit/ingestion/test_parsers.py`
- `tests/unit/ingestion/test_parse_service.py`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `README.md`
- `_bmad-output/implementation-artifacts/2-3-pdf-docx-parser-与页码-metadata.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
