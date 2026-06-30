---
status: Accepted
date: 2026-06-30
deciders: Architecture Team
---

# ADR 0008: API 版本化策略 — URL 前缀 + 向后兼容

## Status

Accepted

## Context

AegisRAG API 当前所有端点均直接暴露在根路径下（如 `/auth/login`、`/chat`、`/upload`），没有任何版本标识。随着系统演进，未来对 API 的破坏性变更将不可避免（字段重命名、端点重构、响应格式调整等）。若无版本化策略，任何破坏性变更都会直接影响所有客户端。

核心设计约束：

- 客户端（前端 SPA、外部集成方、`service_token` OpenAI 兼容端点）需要稳定的 API 契约
- 迁移窗口必须充分，旧路由不能立即移除
- 实现应最小化对现有路由文件（`apps/api/routes/*.py`）的侵入——版本化应在应用组装层（`main.py`）完成
- OpenAI 兼容端点（`/v1/models`、`/v1/chat/completions`）的路径已经是其 API 规范的一部分，不应添加额外前缀

## Decision

采用 **URL 路径前缀版本化**（`/v1/`）策略，并在 `main.py` 的应用组装层实现：

### 版本化路由结构

```
v1_router (prefix="/v1")
  ├── health_router      → /v1/health, /v1/ready
  ├── auth_router        → /v1/auth/login, /v1/auth/refresh
  ├── chat_router        → /v1/chat, /v1/chat/history, /v1/chat/stream
  ├── upload_router      → /v1/upload
  ├── documents_router   → /v1/documents/...
  ├── retrieve_router    → /v1/retrieve
  ├── query_router       → /v1/query, /v1/query/stream
  ├── agent_router       → /v1/agent/run
  ├── groups_router      → /v1/groups/...
  ├── users_router       → /v1/users/...
  ├── eval_evidence_router → /v1/eval/...
  ├── review_queue_router  → /v1/review/...
  ├── audit_explorer_router → /v1/audit/...
  ├── sources_router     → /v1/sources/...
  ├── diagnostics_router → /v1/diagnostics/...
  ├── sidecar_router     → /v1/sidecar
  └── governance_router  → /v1/governance
```

**特例：`service_token` 路由器已在路径中使用 `/v1/`（OpenAI API 兼容格式）**，因此不纳入版本化路由——其端点保持为 `/v1/models` 和 `/v1/chat/completions`，无需变更。

### 向后兼容

所有旧路由（无 `/v1` 前缀）**保持可用**，但被视为已弃用。`DeprecationMiddleware`（`apps/api/middleware.py`）会为这些旧路径自动注入响应头：

| Header | Value |
|--------|-------|
| `X-API-Deprecated` | `true` |
| `X-API-Version` | `v1` |

**豁免路径**（始终不注入弃用头）：`/v1/*`、`/health`、`/ready`、`/metrics`、`/sidecar`、`/sidecar/*`

### 版本发现端点

新增 `GET /api/version`，返回当前 API 版本元数据：

```json
{
  "code": "SUCCESS",
  "data": {
    "api_version": "v1",
    "app_version": "0.2.0",
    "deprecated_routes_available": true
  }
}
```

### 中间件顺序

为确保旧路由的弃用头在所有响应中可见，`DeprecationMiddleware` 作为最外层中间件注册，优先级高于 `RateLimitMiddleware` 和 `RequestLoggingMiddleware`。

### 限流路径同步更新

`RateLimitMiddleware` 的 `path_limits` 配置同时包含新旧两组路径：

```python
path_limits={
    "/auth/login":       login_rate_limit_config,
    "/auth/refresh":     refresh_rate_limit_config,
    "/v1/auth/login":    login_rate_limit_config,
    "/v1/auth/refresh":  refresh_rate_limit_config,
}
```

## Consequences

**正面影响：**

- 未来破坏性变更可以通过新增 `/v2/` 路由实现，旧客户端继续使用 `/v1/`
- 路由文件（`apps/api/routes/*.py`）无需任何修改——版本化在组装层完成
- `DeprecationMiddleware` 为旧路径使用者提供明确迁移信号
- `/api/version` 端点允许客户端在运行时自动发现 API 版本
- 对现有业务逻辑零侵入

**负面影响：**

- URL 命名空间中存在两套等效路由（`/v1/chat` 和 `/chat`），OpenAPI schema 中会出现重复条目
- `DeprecationMiddleware` 基于路径前缀匹配，与路由注册解耦——如果将来添加新的豁免路径，需要手动更新 `_DEPRECATION_EXEMPT_PREFIXES`

**当前权衡：**

- 未引入请求级版本协商（如 `Accept: application/vnd.aegisrag.v1+json` header），优先采用最简单直观的 URL 前缀方案
- 旧路由通过 middleware 标记弃用而非返回 `301 Moved Permanently`——减少客户端迁移的断崖式压力

## Deprecation Timeline

| 阶段 | 时间 | 行为 |
|------|------|------|
| **Phase 1** (当前) | 2026-Q3 | 旧路由可用，`X-API-Deprecated: true` 头自动注入 |
| **Phase 2** | 2026-Q4 | 旧路由返回 `Warning` 头 + 弃用头，文档中标记为"即将移除" |
| **Phase 3** | 2027-Q1 | 旧路由返回 `410 Gone`，仅 `/v1/` 路由可用 |

具体时间线以实际发布节奏为准，可能根据客户端迁移进度调整。

## Alternatives Considered

| 方案 | 评估 |
|------|------|
| Header-based versioning (`Accept: vnd.aegisrag.v1+json`) | RESTful 但实现复杂，客户端调试困难（curl 中不可见），不适合以开发者工具为主要使用场景的系统 |
| Query parameter (`?version=v1`) | 简单但语义错误——版本是资源标识的一部分，不应作为查询参数 |
| 仅靠文档约定，不做代码级版本化 | 零成本但不可执行——依赖所有人遵守约定，版本冲突不可避免 |
| 立即移除旧路由（无兼容期） | 对现有客户端造成断崖式破坏，不可接受 |
| 每个路由文件内手动添加 `/v1` | 侵入性强、容易遗漏、未来切换 `/v2` 时需修改所有文件 |
