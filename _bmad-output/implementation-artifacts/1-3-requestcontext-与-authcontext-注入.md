---
baseline_commit: NO_VCS
---

# Story 1.3: RequestContext 与 AuthContext 注入

Status: done

生成时间：2026-05-27T09:00:51+08:00

## Story

As a 后端开发者,
I want API 层统一生成 `RequestContext` 和 `AuthContext`,
so that 所有业务服务都能显式接收 `user_id`、`tenant_id`、roles 和 permissions。

## Acceptance Criteria

1. **缺少认证上下文时拒绝业务 endpoint**
   - Given 请求缺少 `tenant_id` 或 `user_id`
   - When 访问需要认证上下文的业务 endpoint
   - Then API 返回结构化错误 `AUTH_CONTEXT_REQUIRED`
   - And application service 不会被调用

2. **JWT、开发 header 和测试 fixture 产出同一 DTO**
   - Given 请求包含 JWT bearer token、开发模拟上下文 header 或测试 fixture 上下文
   - When route 调用 application service
   - Then service 接收到类型化 `RequestContext`
   - And `AuthContext` 至少包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions`
   - And JWT adapter 与模拟 AuthContext parser 必须产出相同的 `AuthContext` DTO

3. **权限过滤基础结构可由 AuthContext 构建**
   - Given 后续 retrieval 或 tool policy 需要权限过滤
   - When 调用 auth policy builder
   - Then 能从 `AuthContext` 生成 tenant、RBAC、ACL 和 metadata filter 的基础结构
   - And 不把权限规则拼进 prompt

## Tasks / Subtasks

- [x] 定义上下文 DTO 与认证异常（AC: 1, 2）
  - [x] 新增 `packages/auth/context.py`，定义 Pydantic v2 `AuthContext`：`user_id: str`、`tenant_id: str`、`roles: tuple[str, ...]`、`department: str | None`、`permissions: tuple[str, ...]`。
  - [x] `AuthContext` 必须校验 `user_id` 和 `tenant_id` 非空；roles/permissions 使用不可变 tuple，避免请求间可变默认值泄漏。
  - [x] 新增 `packages/common/context.py`，定义 `RequestContext` 和 `AuthenticatedRequestContext`：`request_id`、`trace_id`、`session_id | None`，认证版本还必须包含非空 `auth: AuthContext`。
  - [x] 新增 `packages/auth/exceptions.py`，定义稳定错误码异常，例如 `AuthContextRequiredError(code="AUTH_CONTEXT_REQUIRED")`；可以预留 `AUTH_CONTEXT_INVALID`，但本 Story 的缺失场景必须返回 `AUTH_CONTEXT_REQUIRED`。
  - [x] DTO 模块不得导入 FastAPI、SQLAlchemy、Redis、MinIO、LLM SDK 或外部 provider SDK。

- [x] 实现 AuthContext parser 和 JWT adapter（AC: 1, 2）
  - [x] 新增 `packages/auth/parsers.py`，提供从开发 header、JWT claims 和测试 fixture dict 构造同一 `AuthContext` 的纯函数或 parser 类。
  - [x] 开发模拟 header 使用稳定命名：`X-User-ID`、`X-Tenant-ID`、`X-Roles`、`X-Department`、`X-Permissions`；roles/permissions 可用逗号分隔，并需去空白、过滤空项。
  - [x] JWT claims 映射规则必须稳定：`sub` 或 `user_id` -> `user_id`，`tenant_id` -> `tenant_id`，`roles` -> roles，`department` -> department，`permissions` 或 `scope` -> permissions。
  - [x] 如实现真实 JWT bearer 验证，使用 `PyJWT` 并通过环境变量配置 secret / algorithm / issuer / audience；不得接受未验证 JWT 作为生产认证。
  - [x] 未配置 JWT secret 时，bearer token 不得被静默信任；测试可使用 dependency override 或测试 settings 注入密钥。
  - [x] 解析失败、缺少 `user_id` 或缺少 `tenant_id` 时必须转成领域认证异常，不把原始 token 写入错误 details。

- [x] 建立 FastAPI dependency 注入点（AC: 1, 2）
  - [x] 新增 `apps/api/dependencies.py`，提供 `get_request_context()`、`get_auth_context()`、`get_authenticated_request_context()` 以及 `Annotated` 类型别名，例如 `RequestContextDep`、`AuthenticatedRequestContextDep`。
  - [x] `get_request_context()` 统一解析 `X-Request-ID`、`X-Trace-ID`、`X-Session-ID`；缺少 request/trace id 时生成 UUID 字符串。
  - [x] 更新 `apps/api/routes/health.py`，移除私有 `_resolve_request_id` 重复逻辑，改用 `RequestContextDep`；`/health` 和 `/ready` 保持公开，不要求 `AuthContext`。
  - [x] 需要认证的后续业务 route 必须依赖 `AuthenticatedRequestContextDep`，不得在 route 内手动解析 header 或 token。
  - [x] dependency 只做上下文解析和校验，不访问数据库、Redis、MinIO、向量库、LLM、embedding provider 或网络。

- [x] 返回结构化认证错误且阻止 service 调用（AC: 1）
  - [x] 新增或更新 `apps/api/error_handlers.py`，只注册本 Story 需要的认证上下文异常处理；不要抢先实现全局异常处理、审计日志或 structured logging 全套能力。
  - [x] 认证缺失响应必须保持统一 envelope：`request_id`、`data: null`、`error.code == "AUTH_CONTEXT_REQUIRED"`、`metadata`。
  - [x] HTTP 状态建议使用 `401`；错误 message 不得暴露是否存在某个 tenant、user、文档或权限资源。
  - [x] integration 测试必须证明缺少认证上下文时 application service/stub 不会被调用。

- [x] 实现权限过滤基础 builder（AC: 3）
  - [x] 新增 `packages/auth/policies.py`，定义 `AccessFilter` 或等价 DTO，至少包含 `tenant_id`、`user_id`、`roles`、`department`、`permissions`、`metadata_filter`、`acl_filter`。
  - [x] 提供 `build_access_filter(auth: AuthContext) -> AccessFilter`，输出结构化 filter，供后续 retrieval/tool policy 使用。
  - [x] filter 必须是数据结构，不得生成 prompt 文本，不得让 LLM 判断权限。
  - [x] 单测覆盖跨 tenant filter、permissions 映射、空 roles/permissions 和 department 缺失。

- [x] 补充测试（AC: 1, 2, 3）
  - [x] 新增 `tests/unit/auth/test_context.py`，覆盖 `AuthContext` 必填字段、tuple 序列化、空 `user_id`/`tenant_id` 拒绝。
  - [x] 新增 `tests/unit/auth/test_parsers.py`，覆盖开发 header parser、JWT claims parser、测试 fixture parser 输出同一 `AuthContext`。
  - [x] 新增 `tests/unit/auth/test_policies.py`，覆盖 `build_access_filter()` 结构，不允许 prompt 字符串替代权限规则。
  - [x] 新增或更新 `tests/unit/common/test_context.py`，覆盖 `RequestContext` / `AuthenticatedRequestContext` 字段和序列化。
  - [x] 新增 `tests/integration/api/test_context_dependencies.py`，使用测试内临时 FastAPI route 或依赖注入测试 app 验证：缺少上下文返回 envelope 错误、service spy 未调用、开发 header 成功、JWT bearer/test fixture 成功。
  - [x] 更新 `tests/integration/api/test_health_routes.py`，确保 `/health` 和 `/ready` 仍公开、仍回显或生成 `request_id`，并通过新的 `RequestContext` dependency。
  - [x] 保持 `tests/unit/test_architecture_boundaries.py` 通过；如果新增 FastAPI dependency 文件被边界测试误判，优先调整测试白名单到 `apps/api/dependencies.py`，不要把 FastAPI 导入 `packages/*`。

- [x] 更新文档和验证命令（AC: 1, 2, 3）
  - [x] 更新 `README.md`，说明 `RequestContext` / `AuthContext` 最小 header、JWT claims 映射、缺失认证上下文的 envelope 错误形状。
  - [x] 更新 `docs/operations/local-development.md`，补充本地开发如何显式启用模拟 AuthContext header；生产默认不得信任开发 header 或未验证 JWT。
  - [x] 如新增 `PyJWT`，更新 `pyproject.toml` / `uv.lock`，并记录版本范围；不要硬编码 JWT secret。
  - [x] 运行 `uv run pytest`。
  - [x] 运行 `uv run ruff check .`。
  - [x] 运行 `uv run mypy apps packages tests`。
  - [x] 若任一命令未执行或失败，必须在 Dev Agent Record 中记录原因，不得把未通过的验证描述为通过。

### Review Findings

- [x] [Review][Patch] 空白 `X-Request-ID` 或 `X-Trace-ID` 会触发 `RequestContext` 校验异常并让公开 endpoint 返回 500，应在 dependency 中 strip 后为空则生成 UUID，或返回结构化客户端错误。[apps/api/dependencies.py:31]
- [x] [Review][Patch] 集成测试中的 application service spy 只接收 `AuthContext`，未证明 route 会把类型化 `AuthenticatedRequestContext` 传给 service；应让 spy 接收完整上下文并断言 `request_id`、`trace_id`、`session_id` 和 `auth`。[tests/integration/api/test_context_dependencies.py:21]
- [x] [Review][Patch] 开发模拟认证 header 只由 `ENABLE_DEV_AUTH_HEADERS` 单一开关控制，生产误配一个环境变量即可伪造任意 `user_id`、`tenant_id`、roles 和 permissions；应增加 local/test 环境门禁并补禁用态回归测试。[apps/api/dependencies.py:49]
- [x] [Review][Patch] JWT 解码未要求 `exp` claim，缺少过期时间的 bearer token 会被接受；应在 PyJWT options 中 require `exp` 并补负向测试。[packages/auth/parsers.py:93]
- [x] [Review][Patch] JWT 中显式空 `permissions` claim 会 fallback 到 `scope`，可能把调用方明确清空的权限重新授予；应只在 `permissions` claim 缺失时才使用 `scope`。[packages/auth/parsers.py:55]
- [x] [Review][Patch] JWT 同时包含 `sub` 和 `user_id` 且两者不同值时会静默选择 `sub`；应拒绝冲突 claim，避免身份来源歧义。[packages/auth/parsers.py:52]
- [x] [Review][Patch] `AuthContext` 的 `roles` / `permissions` validator 对非字符串、非可迭代输入可能抛 raw `TypeError`，未稳定映射为 Pydantic/domain validation error；应显式校验输入形态并抛 `ValueError`。[packages/auth/context.py:23]
- [x] [Review][Patch] `AccessFilter` 标记 frozen，但 `metadata_filter` / `acl_filter` 仍是可变 dict，可在创建后被改写；应冻结嵌套结构或避免暴露可变 mapping。[packages/auth/policies.py:14]
- [x] [Review][Patch] `build_access_filter()` 把 `department` 放入 `metadata_filter`，可能在正式 ACL 判断前过滤掉共享文档或跨部门授权文档；应只把 tenant 放入强制 metadata filter，把 department 留在 ACL/filter facts 中。[packages/auth/policies.py:18]
- [x] [Review][Patch] 架构边界测试将 `apps/api/dependencies.py` 和 `apps/api/error_handlers.py` 整文件跳过，未来在这些文件声明 `APIRouter` 或 route decorator 也会通过测试；应只允许这些文件导入 FastAPI，不允许声明 routes。[tests/unit/test_architecture_boundaries.py:20]
- [x] [Review][Defer] 完整 tenant membership 校验需要用户/租户/角色持久化与 RBAC 数据源，本 story 只能消费已验证 token/header 中的认证事实。[packages/auth/parsers.py:52] — deferred, pre-existing
- [x] [Review][Defer] `AccessFilter` 还没有表达 public access、deny rule、role intersection、继承等完整 ACL 语义；这是后续 retrieval/tool policy 的设计工作，本 story 只提供基础结构。[packages/auth/policies.py:18] — deferred, pre-existing
- [x] [Review][Defer] JWT 生产级硬化还包括 issuer/audience 是否强制、token purpose/type、algorithm allowlist、JWKS/key rotation 和 auth readiness gate；当前 story 未定义这些策略边界。[packages/auth/parsers.py:85] — deferred, pre-existing
- [x] [Review][Defer] `X-Request-ID` / `X-Trace-ID` 的最大长度、字符集和日志清洗策略尚未定义；空白 header 需要本 story 修复，完整日志安全策略可纳入 Story 1.4。[apps/api/dependencies.py:31] — deferred, pre-existing
- [x] [Review][Defer] 401 响应未设置 `WWW-Authenticate`，会影响部分 OAuth/Bearer 客户端互操作；当前 envelope 行为满足 story，标准 header 可在认证接口稳定化时补齐。[apps/api/error_handlers.py:44] — deferred, pre-existing

## Dev Notes

### 当前仓库状态

- 当前目录不是 git repository，无法读取 commit 历史；实现模式主要来自 Story 1.1、Story 1.2 和现有代码。
- 现有 API 只有 `GET /health` 和 `GET /ready`，都在 `apps/api/routes/health.py`。
- `apps/api/routes/health.py` 当前有私有 `_resolve_request_id()`，这是 Story 1.2 的临时逻辑；本 Story 应把它收敛到 `apps/api/dependencies.py` 的 `RequestContext` dependency。
- `packages/common/envelope.py` 已存在 `ApiError`、`ResponseMetadata`、`ApiResponse[T]`、`success_response()`、`error_response()`，认证错误必须复用它，不要另造响应格式。
- `packages/common/health.py` 当前是无外部 I/O 的 DTO/service，保持不访问数据库、Redis、MinIO 或向量库。
- `packages/auth` 当前只有 `__init__.py`，可以安全新增 context/parser/policy/exception 模块。
- `tests/unit/test_architecture_boundaries.py` 已要求 FastAPI route 只能在 `apps/api/routes` 或 `apps/api/main.py` 注册入口出现，并禁止 domain 层导入基础设施/框架。

### Source Context

- Epic 1 的目标是让平台负责人获得统一 API 契约、认证上下文、结构化错误、审计日志、健康检查和基础可观测能力，为后续 ingestion、retrieval、RAG 和 Agent 提供安全底座。[Source: `_bmad-output/planning-artifacts/epics.md#Epic 1`]
- Story 1.3 覆盖 FR18 和 FR21：API 必须注入 `RequestContext` / `AuthContext`，缺少 tenant 或 user 的业务请求必须被拒绝。[Source: `_bmad-output/planning-artifacts/epics.md#Story 1.3`]
- PRD 要求 `AuthContext` 包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions`，application service 必须显式接收 `AuthContext` 或 `RequestContext`。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-21`]
- Architecture 要求 `apps/api/dependencies.py` 拥有 `RequestContext` / `AuthContext` 注入；route 只做 schema、认证上下文注入、service 调用和响应封装。[Source: `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`]
- Architecture 固定 MVP 认证方案为开发/测试模拟 AuthContext + 轻量 JWT adapter；两种入口必须产出同一 `AuthContext` DTO，模拟 AuthContext 只能在开发、测试或显式本地配置中启用。[Source: `_bmad-output/planning-artifacts/architecture.md#Authentication & Security`]

### Architecture Requirements

- `apps/api/dependencies.py` 是唯一的 HTTP 层上下文注入入口；后续 upload/retrieve/query/chat/agent route 都应复用它。
- `packages/auth/*` 可以定义领域 DTO、parser、policy 和异常，但不能导入 FastAPI 或响应对象。
- `packages/common/context.py` 可以保存跨 API/application service 的 `RequestContext` DTO；若它引用 `AuthContext`，必须保持纯 DTO 依赖，不得引入 framework 或 infrastructure。
- `AuthContext` 是权限事实输入，不是 prompt 片段；retrieval/tool policy 后续只能消费结构化 filter。
- `RequestContext` 至少要携带 `request_id` 和 `trace_id`，因为后续 Story 1.4 的 structured logging/audit 会依赖它。
- `session_id` 是可选字段，当前不实现 chat memory 或 session persistence；Story 4.6 再落地会话存储。

### Previous Story Intelligence

- Story 1.2 已建立统一 envelope，并明确 Story 1.3 会收敛 request_id 逻辑到 `RequestContext` dependency。
- Story 1.2 的 `success_response()` 和 `error_response()` 是纯 helper，不读取全局请求状态；本 Story 也应显式传递 `request_id`。
- Story 1.2 不实现全局异常处理、中间件、审计日志或真实依赖探活；本 Story 只实现认证上下文所需的最小错误处理，不要提前做 Story 1.4。
- 现有测试命令在上一条故事完成时均通过：`uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`。
- `pyproject.toml` 当前已有 `pydantic-settings`，如果需要通过环境变量控制 dev header/JWT 配置，优先复用它，不要硬编码配置。

### File Structure Guidance

建议本 Story 最小落地文件集：

```text
apps/api/main.py                            # UPDATE: 注册 auth error handler
apps/api/dependencies.py                    # NEW: RequestContext/AuthContext dependency
apps/api/error_handlers.py                  # NEW: 仅认证上下文错误的 envelope handler
apps/api/routes/health.py                   # UPDATE: 使用 RequestContextDep，移除重复 request_id helper
packages/common/context.py                  # NEW: RequestContext / AuthenticatedRequestContext DTO
packages/auth/context.py                    # NEW: AuthContext DTO
packages/auth/exceptions.py                 # NEW: AuthContextRequiredError 等稳定错误码
packages/auth/parsers.py                    # NEW: dev header / JWT claims / fixture parser
packages/auth/policies.py                   # NEW: AccessFilter + build_access_filter
tests/unit/common/test_context.py           # NEW
tests/unit/auth/test_context.py             # NEW
tests/unit/auth/test_parsers.py             # NEW
tests/unit/auth/test_policies.py            # NEW
tests/integration/api/test_context_dependencies.py # NEW
tests/integration/api/test_health_routes.py # UPDATE
README.md                                   # UPDATE
docs/operations/local-development.md        # UPDATE
pyproject.toml / uv.lock                    # UPDATE only if adding PyJWT
```

不要新增生产业务 endpoint 只为测试认证。需要验证 dependency 行为时，优先在 integration test 内创建临时 FastAPI app 或测试 route，并用 spy/stub application service 证明 dependency 通过后才调用 service。

### Implementation Boundaries

- 不要实现完整 RBAC policy engine、数据库用户/角色表、JWT key rotation、JWKS、SSO、Open WebUI adapter 或多租户管理后台。
- 不要实现 upload/retrieve/query/chat/source/agent endpoint。
- 不要实现 retrieval ACL filter 的数据库查询；本 Story 只产出后续 retrieval/tool 可以消费的 filter DTO。
- 不要实现全局 unexpected exception handler、structured logging、audit log、中间件、latency 统计或 OpenTelemetry；这些属于 Story 1.4 或后续故事。
- 不要让 `/health` 或 `/ready` 要求认证；它们是本地开发和部署探活基础。
- 不要把权限逻辑写进 prompt、自然语言 policy 或 LLM 调用。
- 不要在日志、错误 details、测试快照或文档示例中写真实 access token、API key、企业机密全文。

### Auth Contract

开发 header 示例：

```text
X-Request-ID: req-local-1
X-Trace-ID: trace-local-1
X-User-ID: user-123
X-Tenant-ID: tenant-abc
X-Roles: admin,knowledge_manager
X-Department: HR
X-Permissions: document:read,retrieval:query
```

JWT claims 示例：

```json
{
  "sub": "user-123",
  "tenant_id": "tenant-abc",
  "roles": ["admin", "knowledge_manager"],
  "department": "HR",
  "permissions": ["document:read", "retrieval:query"]
}
```

缺少认证上下文的 envelope 示例：

```json
{
  "request_id": "req-123",
  "data": null,
  "error": {
    "code": "AUTH_CONTEXT_REQUIRED",
    "message": "Authentication context is required.",
    "details": {}
  },
  "metadata": {
    "latency_ms": null
  }
}
```

`details` 可以包含安全字段名摘要，例如 `{"missing": ["tenant_id"]}`，但不得包含 token、完整 header、绝对文件路径或资源存在性信息。

### Testing Requirements

- 测试默认禁止真实外部 LLM、embedding、向量库、数据库、Redis、MinIO 或网络调用。
- JWT 测试必须使用测试密钥和测试 claims；不要把未验证 token 接收逻辑作为生产默认路径。
- integration test 需要明确证明缺少 `tenant_id` 或 `user_id` 时业务 service/stub 没有被调用。
- `RequestContext` 测试要覆盖有/无 `X-Request-ID`、有/无 `X-Trace-ID`、可选 `X-Session-ID`。
- Parser 测试要覆盖 roles/permissions 为空、逗号分隔、列表 claims、`scope` 字符串 claims。
- Policy builder 测试要断言输出是结构化 dict/model，例如 `tenant_id == auth.tenant_id`，而不是拼好的 prompt 或自然语言。
- 保持 `uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests` 全绿。

### Latest Technical Information

- 2026-05-27 通过 PyPI JSON 复核：FastAPI 当前版本为 `0.136.3`，Pydantic 当前版本为 `2.13.4`，与当前 `pyproject.toml` 版本范围一致。
- 2026-05-27 通过 PyPI JSON 复核：PyJWT 当前版本为 `2.13.0`。如本 Story 增加 JWT 验证依赖，建议使用 `PyJWT>=2.13.0,<3`，并用环境变量配置 secret/algorithm；不要在代码中硬编码 secret。
- FastAPI 官方文档说明 dependency injection 用于共享逻辑和执行 security/authentication/role requirements，适合承载 `RequestContext` / `AuthContext` 注入。
- FastAPI `HTTPBearer(auto_error=False)` 可在 bearer token 缺失时返回 `None`，适合同时支持 bearer token 与开发 header 两种可选认证来源。
- PyJWT 官方文档包含 HS256/RS256 的 encode/decode 用法，也包含“未验证读取 claims”的示例；本项目不得把未验证读取作为生产认证路径。

### UX / Frontend Notes

- UX 文档要求前端永远只展示后端确认过的状态，不判断权限、不补造引用、不推断检索结果。`AuthContext` 是这个规则的后端基础。[Source: `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Business Fine-Tuning Rules`]
- 缺少认证上下文时，UI 应只看到通用权限信息和 `request_id`，不暴露 tenant、user、文档或资源是否存在。
- 本 Story 不需要新增前端 UI。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 1.3`
- `_bmad-output/planning-artifacts/architecture.md#Authentication & Security`
- `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-21`
- `_bmad-output/implementation-artifacts/1-2-统一-api-envelope-与健康检查.md#Dev Notes`
- `https://fastapi.tiangolo.com/tutorial/dependencies/`
- `https://fastapi.tiangolo.com/reference/security/#fastapisecurityhttpbearer`
- `https://pyjwt.readthedocs.io/en/stable/usage.html`
- `https://pypi.org/project/PyJWT/`

## Validation Checklist

Validation Result: PASS（2026-05-27T09:00:51+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 来自 Epic Story 1.3，覆盖缺失认证拒绝、统一 DTO 注入和权限 filter 基础结构。
- [x] Tasks 覆盖 AC 1 到 AC 3，并标注 AC 映射。
- [x] Dev Notes 包含当前代码状态、架构边界、上一条 Story 经验、推荐文件位置、测试要求和实现边界。
- [x] 明确说明本 Story 不实现完整 RBAC、数据库用户表、SSO、Open WebUI adapter、retrieval 查询、全局日志/审计或业务 endpoint。
- [x] 明确禁止生产信任未验证 JWT，并要求 dev header 只能在开发/测试或显式本地配置中启用。
- [x] 包含当前 FastAPI/Pydantic/PyJWT 版本复核结果和官方文档参考。
- [x] File Structure Guidance 指向现有代码可安全扩展的位置，避免 route、dependency、auth/common DTO 越界。

## Change Log

- 2026-05-27: Created comprehensive Story 1.3 developer context for RequestContext and AuthContext injection.
- 2026-05-27: Implemented RequestContext/AuthContext injection, verified JWT/dev header parsing, auth error envelope, access filter builder, tests, and documentation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-05-27: `uv run pytest tests/unit/auth/test_context.py tests/unit/common/test_context.py` PASS（14 passed）。
- 2026-05-27: `uv run pytest` PASS（29 passed）。
- 2026-05-27: `uv run pytest tests/unit/auth/test_parsers.py` PASS（11 passed）。
- 2026-05-27: `uv run pytest` PASS（40 passed）。
- 2026-05-27: `uv run pytest tests/integration/api/test_context_dependencies.py tests/integration/api/test_health_routes.py tests/unit/test_architecture_boundaries.py` PASS（12 passed）。
- 2026-05-27: `uv run pytest` PASS（44 passed）。
- 2026-05-27: `uv run pytest tests/integration/api/test_context_dependencies.py tests/integration/api/test_health_routes.py` PASS（10 passed）。
- 2026-05-27: `uv run pytest` FAIL（45 passed, 1 failed）：`apps/api/error_handlers.py` 触发架构边界白名单；已仅将该 API 层文件加入白名单。
- 2026-05-27: `uv run pytest` PASS（46 passed）。
- 2026-05-27: `uv run pytest tests/unit/auth/test_policies.py` PASS（4 passed）。
- 2026-05-27: `uv run pytest` PASS（50 passed）。
- 2026-05-27: `uv run pytest tests/unit/auth/test_context.py tests/unit/auth/test_parsers.py tests/unit/auth/test_policies.py tests/unit/common/test_context.py tests/integration/api/test_context_dependencies.py tests/integration/api/test_health_routes.py tests/unit/test_architecture_boundaries.py` PASS（44 passed）。
- 2026-05-27: `uv run pytest` PASS（51 passed）。
- 2026-05-27: `uv run pytest` PASS（51 passed）。
- 2026-05-27: `uv run ruff check .` FAIL：长行、`packages/auth/parsers.py` import 顺序和未使用导入；已修复。
- 2026-05-27: `uv run ruff check .` PASS。
- 2026-05-27: `uv run mypy apps packages tests` FAIL：动态 Pydantic 校验和 PyJWT options 类型需要更精确表达；已改为 `model_validate()` 和 `jwt.types.Options`。
- 2026-05-27: `uv run mypy apps packages tests` PASS。
- 2026-05-27: Final `uv run pytest` PASS（51 passed）。
- 2026-05-27: Final `uv run ruff check .` PASS。
- 2026-05-27: Final `uv run mypy apps packages tests` PASS（32 source files）。
- 2026-05-27: Completion gate `uv run pytest` PASS（51 passed）。
- 2026-05-27: Code review patches applied; targeted `uv run pytest tests/unit/auth/test_context.py tests/unit/auth/test_parsers.py tests/unit/auth/test_policies.py tests/integration/api/test_context_dependencies.py tests/unit/test_architecture_boundaries.py` PASS（42 passed）。
- 2026-05-27: Code review completion `uv run pytest` PASS（61 passed）。
- 2026-05-27: Code review completion `uv run ruff check .` PASS。
- 2026-05-27: Code review completion `uv run mypy apps packages tests` PASS（32 source files）。

### Completion Notes List

- 已实现纯 DTO 层 `AuthContext`、`RequestContext`、`AuthenticatedRequestContext` 和认证上下文领域异常；DTO 层未引入 FastAPI、数据库、Redis、MinIO、LLM 或 provider SDK。
- 已通过单测覆盖必填字段、空白拒绝、tuple 默认值和稳定错误码。
- 已实现开发 header、JWT claims、fixture dict 到同一 `AuthContext` DTO 的 parser；bearer token 仅通过 PyJWT 验证后转换为认证上下文，未配置 secret 时不会信任 token。
- 已新增 FastAPI dependency 注入入口，统一生成 `RequestContext` / `AuthenticatedRequestContext`，并将 `/health`、`/ready` 切换为复用 `RequestContextDep`，保持公开探活不需要认证。
- 已注册最小认证上下文异常处理器，缺少或无效认证上下文返回统一 envelope 和 401；集成测试已证明缺少 `tenant_id` 或认证上下文时业务 service/stub 不会被调用。
- 已实现结构化 `AccessFilter` builder，后续 retrieval/tool policy 可直接消费 tenant、ACL、metadata 和 permissions 过滤事实，不依赖 prompt 或 LLM 判断权限。
- 已补齐 Story 1.3 指定的单元测试、集成测试和架构边界回归；`/health`、`/ready` 已明确覆盖无需认证上下文。
- 已更新 README 和本地开发文档，记录 request/auth header、JWT claims 映射、显式启用开发 header、缺失认证 envelope 和 PyJWT 验证要求。
- 已修复代码审查发现的 10 个 patch 项：空白 request/trace header、完整上下文 service 注入测试、dev header 环境门禁、JWT `exp` 要求、permissions/scope fallback、subject claim 冲突、roles/permissions 输入形态、AccessFilter 嵌套不可变、department metadata 预过滤和架构边界白名单。

### File List

- `_bmad-output/implementation-artifacts/1-3-requestcontext-与-authcontext-注入.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `apps/api/dependencies.py`
- `apps/api/error_handlers.py`
- `apps/api/main.py`
- `apps/api/routes/health.py`
- `docs/operations/local-development.md`
- `packages/auth/context.py`
- `packages/auth/exceptions.py`
- `packages/auth/parsers.py`
- `packages/auth/policies.py`
- `packages/common/context.py`
- `pyproject.toml`
- `README.md`
- `tests/integration/api/test_context_dependencies.py`
- `tests/integration/api/test_health_routes.py`
- `tests/unit/auth/__init__.py`
- `tests/unit/auth/test_context.py`
- `tests/unit/auth/test_parsers.py`
- `tests/unit/auth/test_policies.py`
- `tests/unit/common/__init__.py`
- `tests/unit/common/test_context.py`
- `tests/unit/test_architecture_boundaries.py`
- `uv.lock`
