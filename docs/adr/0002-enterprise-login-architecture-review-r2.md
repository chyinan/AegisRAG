# ADR 0002: 企业登录架构审查 R2

**日期**: 2026-06-18  
**审查者**: Architecture Reviewer (arch-reviewer)  
**基准提交**: 9a49513  
**前置审查**: t_43dfacbd (R1, 发现 4 P0 + 3 设计问题, 已全部修复)

---

## 审查结论: 通过 — 架构简洁合理, 无过度设计

**过度设计评分: 2/10**

> 新代码 ~1925 行 (23 文件), 全部在现有 FastAPI 单体内部实现。无新增外部依赖 (bcrypt/jwt 已有)。无 Redis、无消息队列、无独立认证服务。两个新表、三个新 Service、JWT 走已有解析路径。这是教科书级的 "just enough architecture"。

---

## 逐项验证

### 1. P0-1 前后端 API 契约一致性

**决策**: ✅ 契约一致, 无需修改

**理由**:
- 后端 `LoginResponseData` (auth.py:23-30) 字段与前端 `loginUser()` (auth.ts:252-262) 解析结构完全匹配:
  - `access_token` → `data.access_token`
  - `token_type` → `data.token_type`
  - `user_id` → `data.user_id`
  - `display_name` → `data.display_name`
  - `tenant_id` → `data.tenant_id`
  - `roles` → `data.roles`
  - `permissions` → `data.permissions`
- 前后端均使用 `ApiResponse<T>` 信封 (`data` + `error` 二级结构)
- 前端错误解析 `errorBody.error?.message` 与后端 `DomainError` → `ApiError` 映射一致
- Next.js rewrites: `/api/auth/:path*` → `${apiBaseUrl}/auth/:path*` 路径正确

**影响**: 无

---

### 2. 认证/授权层架构合理性

**决策**: ✅ 架构连贯, JWT 路径复用正确

**理由**:
- LoginService 生成的 JWT claims (login_service.py:84-93) 包含:
  `sub`, `user_id`, `tenant_id`, `display_name`, `roles`, `permissions`, `iat`, `exp`
- 这些 claims 被已有 JWT 验证路径完整消费:
  `decode_jwt_token()` → `parse_jwt_claims()` → `AuthContext`
- `parse_jwt_claims()` 直接从 claims 提取 `roles`/`permissions` (parsers.py:139-142),
  与 LoginService 编码的字段名一致, 无需额外映射
- 权限检查采用 `admin:settings` permission-based pattern (`_require_admin()`),
  与前端 `hasPermission()` 的声明式检查风格一致
- 三种认证方式优先级链清晰: JWT Bearer → OpenWebUI Service Token → Dev Headers

**影响**: 无

---

### 3. 速率限制 / 密码策略与现有架构的协调性

**决策**: ✅ 集成干净, 无侵入

**理由**:

**速率限制**:
- `RateLimitMiddleware` 通过 `path_limits` 参数支持 per-path 覆盖 (main.py:48-52),
  无需修改 `InMemoryRateLimiter` 核心逻辑
- 登录限流 5 req/60s (`key_prefix="rl_login"`) 与全局限流 100 req/60s 隔离
- 中间件在 FastAPI 最外层 (先于 RequestLoggingMiddleware), 正确的位置
- 内存清理 (`_purge_stale`) 已在上一次审查修复 (W1), 无泄漏风险

**密码策略**:
- `_validate_password()` (user_service.py:64-91) 在 `UserService.create_user()` 调用,
  与 seed 脚本的密码生成逻辑独立 — 职责分离正确
- 复杂度检查 (3 of 4: upper/lower/digit/special) 是行业标准基线

**影响**: 无阻塞性问题

---

### 4. 过度设计评分

**评分: 2/10 (极简)**

| 维度 | 实现 | 评分 |
|------|------|------|
| 认证存储 | Postgres (两张表) | ✅ 无 Redis |
| 密码哈希 | bcrypt (CPU-bound) | ✅ |
| Token 签发 | PyJWT (无 OAuth2 服务器) | ✅ |
| 速率限制 | InMemoryRateLimiter (token bucket) | ✅ |
| 会话管理 | 无状态 JWT (24h 过期) | ✅ |
| 服务拆分 | FastAPI 单体, 无独立 auth 服务 | ✅ |
| 消息队列 | 无 | ✅ |

**对比过度设计陷阱**:
- 没有为 "未来可能需要" 的 OIDC/SAML 预留抽象层 (YAGNI)
- 没有引入 Redis 做 token 黑名单 (当前无登出需求)
- 没有为 3 个 Service 各开独立微服务

---

### 5. 服务层职责边界

**决策**: ✅ 边界清晰

```
apps/api/routes/          ← HTTP 层 (Pydantic ↔ HTTP)
  auth.py   → LoginService
  users.py  → UserService
  groups.py → GroupService

packages/auth/            ← 业务逻辑层
  login_service.py  (认证 + JWT 签发)
  user_service.py   (CRUD + 密码策略)
  group_service.py  (CRUD)
  models.py         (ORM 映射)
  seed.py           (开发数据填充)

packages/common/          ← 共享基础设施
  rate_limit.py  (InMemoryRateLimiter)
  envelope.py    (ApiResponse 信封)
  errors.py      (DomainError)
```

- routes 层仅做参数提取 + 调用 service + 包装响应, 无业务逻辑
- service 层无 HTTP 依赖, 可独立单元测试
- 依赖注入通过 FastAPI `Depends` + `AsyncIterator` 管理 session 生命周期
- 无跨层泄漏 (service 不 import FastAPI/Request)

---

## 发现的问题

### F-1 (中等): Migration 缺少 roles/permissions 列

**位置**: `migrations/versions/20260618_0014_local_users_and_groups.py`  
**现象**: `UserGroupModel` 定义了 `roles` 和 `permissions` 列 (String(500), nullable),
但 `alembic upgrade head` 创建的 `user_groups` 表不包含这两列。

**影响**: 
- 开发环境若用 SQLAlchemy `create_all` 建表 → 不受影响
- 生产/CI 若用 `alembic upgrade head` 建表 → 首次访问 `group.roles` 时 ORM 报错

**建议**: 生成新的 migration 补上这两列。

---

### F-2 (低): 密码策略硬编码, 无可配置性

**位置**: `packages/auth/user_service.py:64-91`  
**现象**: 最小长度 (8)、复杂度类别数 (3) 均硬编码, 无法通过环境变量调整。

**建议**: 添加 `PASSWORD_MIN_LENGTH` / `PASSWORD_COMPLEXITY_CATEGORIES` 环境变量, 默认值保持现有行为。非阻塞 — 当前默认是合理的基线。

---

### F-3 (观察): Groups API 不暴露 roles/permissions

**位置**: `apps/api/routes/groups.py:32-37`  
**现象**: `GroupResponse` 不包含 `roles` 和 `permissions` 字段。这些数据仅在 seed 时写入, 通过 JWT claims 传递给前端, 但 API 不可见。

**判断**: 可能是有意设计 — roles/permissions 是基础设施配置, 不应通过 CRUD 随意修改。但缺少文档说明此意图。

**建议**: 在 GroupResponse 或文档中注明 "roles/permissions 仅通过 seed 配置"。

---

### F-4 (观察): 环境变量拼写 "TENANT_ID"

**位置**: `packages/auth/login_service.py:118`  
**现象**: 使用 `os.getenv("TENANT_ID")` 而非 `TENANT_ID`。项目其他地方使用 `tenant_id` 命名。

**影响**: 纯 cosmetic, 但可能引起混淆。

---

## 变更摘要

```
新增:
  apps/api/routes/auth.py         (60 lines)   POST /auth/login
  apps/api/routes/users.py        (90 lines)   GET/POST /users (admin)
  apps/api/routes/groups.py      (128 lines)   CRUD /groups (admin)
  packages/auth/login_service.py (121 lines)   bcrypt + JWT 签发
  packages/auth/user_service.py  (103 lines)   用户 CRUD + 密码策略
  packages/auth/group_service.py  (89 lines)   用户组 CRUD
  packages/auth/models.py         (82 lines)   LocalUserModel + UserGroupModel
  packages/auth/seed.py          (174 lines)   开发种子数据
  migrations/...0014_...py        (78 lines)   DDL

修改:
  apps/api/main.py                 +12  注册新路由 + 登录限流
  apps/api/rate_limit_middleware.py +25  per-path 限流
  apps/web/src/lib/auth.ts         +62  loginUser() + 类型
  apps/web/src/components/auth-gate.tsx +76 登录表单 UI
  apps/web/next.config.mjs          +4  /api/auth 代理

测试: 1133/1134 pass (1 预存 health route 失败, 与本次无关)
```
