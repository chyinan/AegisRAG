# 聚合审查报告：企业登录功能

**聚合日期**: 2026-06-18
**审查员**: Review Aggregator (Hermes Agent)
**输入来源**:
- Code Reviewer (t_28bc01d3) — 代码质量审查
- Security Reviewer (t_1d36fdce) — 安全审计
- Architecture Reviewer (t_43dfacbd) — 架构审查

---

## Review Score: 0/100

> 计算: 100 - 4×P0(20) - 5×P1(10) - 5×P2(3) = 100 - 80 - 50 - 15 = -45 → 下限 0
> **Score < 90 → 自动路由至 Fix Agent 进行返工。**

---

## 裁决: REJECT

三位审查员一致 REJECT。存在 4 个 P0 阻断性缺陷，包括前端登录完全不可用、无认证的用户管理端点、无密码策略、JWT 缺少授权声明。

---

## P0 — Blocker (必须修复才能合并)

### [P0-1] 前后端 API 契约不匹配 — loginUser() 完全不可用
**来源**: Code Reviewer C1 + Security Reviewer M2 + Architecture Reviewer P0

**影响文件**: `apps/web/src/lib/auth.ts:244-263`, `apps/api/routes/auth.py`

**问题**: 后端返回 `ApiResponse{ data: { access_token, token_type } }`，但前端 `loginUser()` 直接从 `.json()` 解构 `{ user_id, roles, permissions, access_token }`，这些字段在响应顶层不存在。`data.access_token` 和 `data.user_id` 均为 `undefined`。错误路径同样有 bug：访问 `error.message` 但实际路径是 `error.error?.message`（被 ApiResponse 包裹）。

**后果**: 企业登录完全不可用 — 任何用户都无法通过前端登录。

**修复方案**:
1. 修复前端 `loginUser()` 正确解构 `ApiResponse.data.access_token`
2. 在 `LoginResponseData` 中增加 `user_id`、`display_name`、`roles`、`permissions` 字段
3. 修复错误处理路径访问 `json.error?.message` 而非 `json.message`
4. 或在后端 `/auth/login` 响应中直接返回 JWT claims 信息

---

### [P0-2] 用户/组管理端点无认证保护 (CWE-306)
**来源**: Security Reviewer C1 + Code Reviewer C2 + Architecture Reviewer P2

**影响文件**: `apps/api/routes/users.py` (POST /users, GET /users), `apps/api/routes/groups.py` (CRUD /groups)

**问题**: 所有用户和组管理端点仅依赖 `RequestContextDep`（请求上下文），未使用 `AuthContextDep` 或 `AuthenticatedRequestContextDep` 进行身份验证。任何未认证的攻击者可以:
- 列出所有用户（含用户名、邮箱、显示名）
- 创建任意用户（含任意密码）
- 查看/修改/删除所有用户组

**修复方案**: 在所有用户/组管理端点添加 `AuthenticatedRequestContextDep` 依赖，并添加权限检查（如 `admin:settings`）确保仅管理员可操作。

---

### [P0-3] 无密码策略 — 接受空密码和弱密码 (CWE-521)
**来源**: Security Reviewer C2 + Code Reviewer W2

**影响文件**: `apps/api/routes/users.py:20`, `apps/api/routes/auth.py:20`, `packages/auth/user_service.py:29`

**问题**: 密码字段定义为 `min_length=1, max_length=255`，`UserService.create_user()` 完全没有密码强度验证。接受单字符密码（如 `"a"`）、空字符串经 strip 后的单字符、以及极弱密码。

**修复方案**:
1. 设置最小密码长度为 8 字符
2. 添加复杂度要求（至少包含大写、小写、数字、特殊字符中的 3 类）
3. 在 `UserService.create_user()` 中添加 `_validate_password()` 验证

---

### [P0-4] JWT Claims 缺少 roles/permissions — 授权检查全部失败
**来源**: Code Reviewer C3 + Architecture Reviewer (LoginResponseData 过于精简)

**影响文件**: `packages/auth/login_service.py:58-65`, `packages/auth/parsers.py`

**问题**: 登录生成的 JWT payload 仅包含 `{ sub, user_id, tenant_id:"default", display_name, iat, exp }`，不含 `roles` 和 `permissions` 声明。`decode_jwt_token()` 期望这些声明用于授权，导致所有后端权限检查因无 roles/permissions 而失败或授予零权限。

**修复方案**: 在 `login_service.py` 的 JWT payload 中增加 `roles` 和 `permissions` 字段，从数据库查询用户所属组的权限。

---

## P1 — Must Fix (应在合并前修复)

### [P1-1] 种子数据包含硬编码弱密码 (CWE-798)
**来源**: Security Reviewer H2

**影响文件**: `packages/auth/seed.py:32-36`

**问题**: `SEED_USERS` 硬编码了 `admin123`、`editor123`、`viewer123` 等可猜测密码。若在生产环境运行，管理员帐户立即可被攻破。

**修复方案**: 种子密码从环境变量读取（如 `SEED_ADMIN_PASSWORD`）或使用 `secrets.token_urlsafe(16)` 生成随机密码并打印到 stdout。

---

### [P1-2] 登录端点无专用速率限制 (CWE-307)
**来源**: Security Reviewer H1 + Code Reviewer W1

**影响文件**: `apps/api/rate_limit_middleware.py`, `apps/api/main.py`

**问题**: 全局速率限制器（100 请求/60秒）对所有端点一视同仁，登录端点 `/auth/login` 与普通 API 共享令牌桶。攻击者可每分钟发起 100 次（初始突发 200 次）登录尝试。

**修复方案**: 为登录端点添加独立、更严格的速率限制（如 5 次/分钟/IP），并添加帐户锁定机制（连续 N 次失败后锁定）。

---

### [P1-3] 硬编码 Tenant ID
**来源**: Code Reviewer C4

**影响文件**: `packages/auth/login_service.py:61`

**问题**: JWT payload 中 `"tenant_id": "default"` 硬编码，应从用户数据或配置中读取。

**修复方案**: 从用户关联的租户信息或请求上下文中获取 tenant_id。

---

### [P1-4] 用户名枚举 — 创建用户错误消息泄露用户名
**来源**: Code Reviewer C5

**影响文件**: `packages/auth/user_service.py:54`

**问题**: 错误消息 `f'Username "{username}" already exists.'` 直接包含用户名，攻击者可枚举已存在的帐户。

> 注: Security Reviewer 确认登录端点已正确实施帐户枚举防护（用户不存在和密码错误返回相同错误消息），此问题仅限于 `create_user` 端点。

**修复方案**: 使用通用错误消息，如 `"Username is not available."` 或 `"User creation failed."`。

---

### [P1-5] 不同错误码泄露账户状态
**来源**: Code Reviewer W3

**影响文件**: `packages/auth/login_service.py`

**问题**: 无效凭据返回 401、不活跃用户返回 403，不同 HTTP 状态码泄露了哪些帐户存在但被禁用。

**修复方案**: 统一错误响应，不活跃用户登录失败也返回 401 和相同的错误消息，仅在审计日志中记录真实原因。

---

## P2 — Should Fix

### [P2-1] 未配置 CORS 策略
**来源**: Security Reviewer M1
**影响文件**: `apps/api/main.py`
**修复**: 显式配置 `CORSMiddleware`，白名单 `allow_origins=["http://localhost:3000"]`。

### [P2-2] JWT 密钥管理无轮换机制
**来源**: Security Reviewer M3
**影响文件**: `packages/auth/parsers.py:23-38`
**修复**: 支持多个密钥并按 `kid` 选择，或引入 JWKS。

### [P2-3] delete_group 静默 SET NULL 用户
**来源**: Code Reviewer W6
**影响文件**: `packages/auth/group_service.py`
**修复**: 删除组时返回警告信息，告知有多少用户的 group_id 被置空。

### [P2-4] 每个路由重复创建 session_factory
**来源**: Code Reviewer W5
**影响文件**: `apps/api/routes/auth.py`, `users.py`, `groups.py`
**修复**: 在 `dependencies.py` 中创建单一 `session_factory` 并通过 FastAPI Depends 复用。

### [P2-5] 添加 CSRF token 到登录表单
**来源**: Code Reviewer S5
**影响文件**: `apps/web/src/components/auth-gate.tsx`
**修复**: 前端登录表单添加 CSRF token（或使用 SameSite Cookie）。

---

## P3 — Nice to Have (不扣分，记录到 Backlog)

| 编号 | 建议 | 来源 |
|------|------|------|
| P3-1 | 添加 `/auth/me` 端点用于会话恢复 | Architecture Reviewer |
| P3-2 | 移除 PUT/DELETE `/groups/{id}` — demo 用不到 | Architecture Reviewer |
| P3-3 | 合并 `seed.py` 到 Alembic data migration | Architecture Reviewer |
| P3-4 | 添加 `expires_in` 到登录响应 | Code Reviewer S1 |
| P3-5 | 添加 `iss`/`aud` 到 JWT claims（当配置了颁发者/受众时） | Code Reviewer S2 |
| P3-6 | 添加 GET/PUT/DELETE `/users/{id}` 端点 | Code Reviewer S3 |
| P3-7 | 移除 `auth-gate.tsx` 中未使用的 token 状态 | Code Reviewer S4 |
| P3-8 | 添加边界测试（非活跃登录、JWT 过期、令牌篡改、组删除含用户） | Code Reviewer S6 |
| P3-9 | JWT 添加 `jti` 声明（支持令牌撤销） | Security Reviewer L2 |
| P3-10 | JWT 添加 `nbf` 声明 | Security Reviewer L3 |
| P3-11 | `list_users` 默认排除非活跃用户 | Code Reviewer W7 |
| P3-12 | bcrypt 列长度从 `String(255)` 精确到 `String(60)` | Security Reviewer L1 |
| P3-13 | 实现 `group name` → `role` 映射 | Architecture Reviewer |
| P3-14 | `password_hash` 验证无意外 `model_dump()` 泄露 | Code Reviewer W4 |

---

## 冲突解决

### 冲突 1: UserService/GroupService 抽象层去留
- Architecture Reviewer: **建议砍掉** — "直接在路由中写查询"（简化提案 #3）
- Code Reviewer: **明确赞扬** — "Clean separation of models/services/routes"
- Security Reviewer: 未评价

→ **裁决: 保留服务层**。代码模块化分离是正确实践，Code Reviewer 明确肯定。Architecture Reviewer 的简化建议属于主观设计偏好（其过度设计评分仅 3/10，说明整体架构已足够简洁）。此建议降级为 P3（Nice to Have）。

### 冲突 2: 用户/组端点认证优先级
- Security Reviewer: CRITICAL (CWE-306)
- Code Reviewer: CRITICAL
- Architecture Reviewer: P2

→ **裁决: P0**。缺失认证保护是明确的安全漏洞，非架构设计问题。Architecture Reviewer 侧重"过度工程"视角，其低优先级评级不适用于安全漏洞。Security + Code Reviewer 的判断一致且正确。

### 冲突 3: seed.py 处理方式
- Security Reviewer: 从环境变量读取密码（H2 修复方案）
- Architecture Reviewer: 合并到 Alembic data migration（简化提案 #2）

→ **裁决: 两者兼容，非冲突**。可以同时实施：将 seed 逻辑移入 data migration，并使用环境变量替代硬编码密码。分别记录为 P1-1（安全修复）和 P3-3（架构简化）。

---

## 合规项 (已正确实施 — 值得肯定)

| 检查项 | 确认人 |
|--------|--------|
| bcrypt 12 rounds + 恒定时间比较 | Security ✅ |
| JWT 算法混淆防护 (algorithms=[...]) | Security ✅ |
| JWT exp 强制验证 | Security ✅ |
| SQLAlchemy 参数化查询，无 SQL 注入 | Security ✅ |
| 前端 `autoComplete` / `type="password"` | Security ✅ |
| 不活跃用户拦截 (403) | Security ✅ |
| X-Request-ID 全链路追踪 | Security ✅ |
| password_hash 排除于 `_user_dict()` | Code ✅ |
| DB 级 UniqueConstraint (username, group_name) | Code ✅ |
| DomainError → HTTP 状态码映射 | Code ✅ |
| Idempotent seed (skip 逻辑) | Code ✅ |
| i18n 国际化覆盖所有文本 | Code ✅ |
| bcrypt+JWT 选型正确 | Architecture ✅ |
| 78/78 测试通过 | All ✅ |

---

## 统计汇总

| 严重程度 | 数量 | 来源 |
|----------|------|------|
| P0 — Blocker | 4 | Code(4) + Security(2) + Arch(1) |
| P1 — Must Fix | 5 | Code(3) + Security(2) |
| P2 — Should Fix | 5 | Security(2) + Code(2) + Arch(1) |
| P3 — Nice to Have | 14 | 三方累计 |
| 合并去重前总发现数 | 29 | Code(18) + Security(10) + Arch(6) |
| 合并去重后独立发现 | 28 | 5 项被三方/两方共同标记 |

**去重合并项**:
- 前后端 API 契约不匹配 → Code C1 + Security M2 + Arch P0 → P0-1
- 用户/组端点无认证 → Security C1 + Code C2 + Arch P2 → P0-2
- 无密码策略 → Security C2 + Code W2 → P0-3
- 登录无专用速率限制 → Security H1 + Code W1 → P1-2

---

## 下一步

Score = 0/100 < 90 → **自动创建 Fix Agent 任务进行返工。**
