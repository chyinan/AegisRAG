# ADR 0003: 企业登录架构审查 R3

**日期**: 2026-06-18
**审查者**: Architecture Reviewer (arch-reviewer)
**审查提交**: 8a78d08 (Fix Agent R3 返工)
**前置审查**: t_e00db1a7 (R2, over-engineering score 2/10, 发现1个中等issue: migration缺列)

---

## 审查结论: 通过 — 修复均极简合理, 无过度设计引入

**过度设计评分: 2/10** (维持 R2 水平)

> R3 修复了 8 个 R2 发现的问题 (1 P0 + 2 P1 + 5 P2), 新增 ~215 行 Python + ~70 行 TypeScript。
> JWT refresh token 采用纯无状态方案 (无 Redis/DB 存储层), 密码策略仅引入 2 个环境变量。
> 无新增服务、无新增数据库表、无新增基础设施依赖。所有增量均在现有模块边界内。

---

## 逐项验证

### 1. P1-2: JWT Refresh Token 机制 — 是否过度设计

**决策**: ✅ 实现极简, 无过度设计

**验证点**:
- ✅ 无 token 存储层 (无 Redis、无 DB 黑名单表、无 revocation 机制)
- ✅ Refresh token 是纯无状态 JWT, 仅多一个 `"type": "refresh"` claim
- ✅ `verify_refresh_token()` 通过 `options={"require": ["exp", "sub", "type"]}` 强制校验类型, 防止 refresh token 被用作 access token
- ✅ Token 过期时间通过环境变量配置 (`JWT_ACCESS_EXPIRY_SECONDS` 默认 3600, `JWT_REFRESH_EXPIRY_SECONDS` 默认 604800), 无额外配置复杂度
- ✅ 前端 `AuthSession` 类型不包含 `refreshToken` 字段 — 客户端尚未实现 token 刷新循环, 服务端 `/auth/refresh` 端点仅作为未来可用的 API。这是正确的 YAGNI 纪律
- ✅ `refresh()` 方法复用已有 `LoginResult` 返回类型, 无新增响应模型

**架构评估**:

```
LoginService.login()      → access_token (1h) + refresh_token (7d)
LoginService.refresh()    → 验证 refresh_token → 查 DB 确认用户状态 → 签发新 access_token
LoginService.verify_refresh_token() → jwt.decode 带 type 校验 (静态方法, 可独立测试)
```

- refresh 时重新查询用户 (验证存在性+活跃状态) 和组角色/权限 (角色变更即时生效) — 设计正确
- refresh 不轮换 token (返回原 refresh_token) — 对内部企业 RAG 系统是可接受的最简方案

**对比过度设计陷阱 (均已避免)**:
- ❌ 未引入 Redis 做 token 黑名单 (当前无登出/撤销需求)
- ❌ 未引入 `refresh_token` 数据库表做持久化 (JWT 自包含)
- ❌ 未引入 token 轮换 (refresh token rotation) — 对内部系统不必要
- ❌ 未引入 OAuth2 完整授权服务器框架
- ❌ 前端未引入 axios interceptor / silent refresh 循环

**影响**: 无阻塞性问题

---

### 2. P2-5: 密码策略配置化 — 是否过度抽象

**决策**: ✅ 合理, 无过度抽象

**验证点**:
- ✅ 仅引入 2 个环境变量: `AUTH_PASSWORD_MIN_LENGTH` (默认 8), `AUTH_PASSWORD_REQUIRED_CATEGORIES` (默认 3)
- ✅ 错误消息动态反映配置值 (如 `f"Password must be at least {min_length} characters."`)
- ✅ 特殊字符正则从 `[^A-Za-z0-9]` (排他式, 过于宽泛) 改为正向 ASCII 集合 `[!"#$%&'()*+,\-./:;<=>?@\[\\\]^_{|}~]` — 更精确
- ✅ 无策略模式、无多 profile 切换、无配置文件驱动

**架构评估**:

```python
min_length = int(os.getenv("AUTH_PASSWORD_MIN_LENGTH", "8"))
required_categories = int(os.getenv("AUTH_PASSWORD_REQUIRED_CATEGORIES", "3"))
```

两个 `os.getenv()` 调用的极简抽象 — 正确级别。

**对比过度设计陷阱 (均已避免)**:
- ❌ 未引入 `PasswordPolicy` 类/接口/策略模式
- ❌ 未引入 YAML/JSON 配置文件驱动多套密码策略
- ❌ 未引入 per-tenant 差异化密码策略
- ❌ 未引入 zxcvbn 等第三方密码强度库 (当前 3-of-4 分类检查足够)

**影响**: 无阻塞性问题

---

### 3. 服务边界 / 模块依赖清洁度

**决策**: ✅ 边界保持清洁

- `LoginService` 仍在 `packages/auth/` 单体模块内, 无拆分
- 新增 `refresh()` 方法在同一 service 类内, 无新增服务类
- `LoginResult` dataclass 与 service 同文件, 内聚性良好
- `/auth/refresh` 路由复用 `LoginResponseData` 模型, 无新增响应类型
- `user_service.py` / `group_service.py` 仅添加 `commit()` 调用 — 无架构变更
- 无跨层依赖泄漏 (routes → service → models 单向)

**依赖图 (未变)**:
```
apps/api/routes/auth.py  →  packages/auth/login_service.py
                                  ↓
                          packages/auth/models.py (LocalUserModel, UserGroupModel)
                                  ↓
                          packages/auth/parsers.py (JwtAuthSettings, jwt)
```

---

### 4. P0-1: 事务提交修复 — 架构影响

**决策**: ✅ 无架构影响, 纯缺陷修复

- `user_service.py` 和 `group_service.py` 的 `create_user()` / `create()` / `update()` / `delete()` 方法末尾添加 `session.commit()`
- 修复前: `flush()` 将变更刷入会话但未持久化, 事务回滚导致数据丢失
- 修复后: `flush()` → `refresh()` → `commit()` 标准事务生命周期
- 无需引入 Unit of Work 模式或事务管理器 — 当前 Simple DI + 手动 commit 对于此规模项目足够

**影响**: 无

---

### 5. P1-1: Migration 补充列 — 架构影响

**决策**: ✅ 无架构影响, 纯 schema 修复

- 在 `user_groups` 表 DDL 中添加 `roles` 和 `permissions` 列 (String(500), nullable)
- 与 `UserGroupModel` ORM 定义对齐
- 无架构层面的变更

**影响**: 无

---

### 6. P2-1 ~ P2-4: 类型对齐 / 前端超时重试 / 正则修复

**决策**: ✅ 均为合理的增量改进, 无架构影响

- **P2-1**: `LoginResponseData.tenant_id` 从 `str | None` 统一为 `str` — 类型一致性修复
- **P2-2**: 前端 `loginUser()` 添加 `AbortController` (30s 超时) + 指数退避重试 (最多3次) + 4xx 不重试 — 标准前端健壮性改进, 无服务端变更
- **P2-3**: `LoginResult` 中 `roles`/`permissions` 从 `tuple` 统一为 `list` — 与 JSON 序列化兼容, 消除不必要的类型转换
- **P2-4**: 特殊字符正则从排他式改为正向集合 — 更精确的匹配

**影响**: 无

---

## 过度设计评分详表

| 维度 | 当前实现 | 评分 |
|------|---------|------|
| Token 管理 | 无状态 JWT (access + refresh), 无存储层 | ✅ 2 |
| Token 过期 | 环境变量可配 (3600s / 604800s) | ✅ 2 |
| 密码策略 | 2 个环境变量 + 内联验证函数 | ✅ 2 |
| 速率限制 | InMemoryRateLimiter token bucket | ✅ 2 |
| 服务拆分 | FastAPI 单体, 无独立 auth 服务 | ✅ 2 |
| 会话存储 | 无状态, 无 Redis/DB session | ✅ 2 |
| 消息队列 | 无 | ✅ 1 |
| 前端状态管理 | AuthSession 仅含 bearerToken, 无 refresh loop | ✅ 2 |

**综合评分: 2/10 — 极简合理**

---

## 发现的问题

### F-1 (低): login() 与 refresh() 中的 token 构造逻辑存在轻微重复

**位置**: `packages/auth/login_service.py:89-98` 和 `login_service.py:232-241`

**现象**: `login()` 和 `refresh()` 方法中构建 JWT access token claims 的代码几乎相同 (sub, user_id, tenant_id, display_name, roles, permissions, type, iat, exp)。

**判断**: 当前 ~260 行的 service 文件, 重复约 10 行 — 提取共享 helper 属于过早抽象。若文件增长超过 ~400 行或出现第三处 token 签发点, 再考虑提取。

**建议**: 维持现状。当前重复是可接受的 trade-off (代码清晰度 vs DRY)。

---

### F-2 (观察): 中间件 `decode_jwt_token()` 不校验 `"type"` claim

**位置**: `packages/auth/parsers.py:169-191`

**现象**: `decode_jwt_token()` 的 `options={"require": ["exp"]}` 仅要求 `exp` claim, 不要求 `type`。理论上 refresh token (带 `"type": "refresh"`) 可以通过中间件认证。

**实际风险**: 近零。refresh token 不含 `roles` 和 `permissions` claims, `parse_jwt_claims()` 解析出的 `AuthContext` 将具有空 roles/permissions, 后续任何需要权限的操作都会失败。这不是安全漏洞, 只是防御深度不足。

**建议**: 可选添加 `"type": "access"` 校验到 `decode_jwt_token()` 的 require options 中, 但非必须。

---

### F-3 (观察): Refresh token 不轮换 (non-rotating)

**位置**: `packages/auth/login_service.py:250-252`

**现象**: `refresh()` 方法返回的 `refresh_token` 与输入相同, 不签发新 refresh token。

**判断**: 对内部企业 RAG 系统, 这是可接受的最简方案。token 轮换的主要价值是:
- 检测 refresh token 被盗用 (rotation 后旧 token 失效)
- 但本系统无 token 撤销机制, 无黑名单存储, 引入轮换反而需要引入存储层 — 违背极简原则

**建议**: 维持现状。若未来有登出/撤销需求, 届时再评估。

---

## P3 遗留项跟踪

| 编号 | 来源 | 建议 | 本次状态 |
|------|------|------|---------|
| P3-1 | R2 F-3 | Groups API 不暴露 roles/permissions | 未处理 — 不阻塞 |
| P3-2 | R2 F-4 | `TENANT_ID` 环境变量拼写 | 未处理 — cosmetic |
| P3-3 | R2 | 添加 DB 写入端到端测试 | 未处理 — 非架构范畴 |

---

## 变更摘要 (R2 → R3)

```
修改 (8 files, +275 -60):
  apps/api/routes/auth.py            +34  新增 POST /auth/refresh + LoginResponseData 扩展
  apps/web/src/lib/auth.ts           +105  超时/重试 + refresh_token/expires_in 字段
  migrations/...0014_...py            +2  补充 roles/permissions 列
  packages/auth/group_service.py      +3  commit() 修复
  packages/auth/login_service.py     +164 refresh() + verify_refresh_token() + 可配过期
  packages/auth/user_service.py      +16  commit() 修复 + 密码策略环境变量
  tests/integration/api/test_auth_routes.py  +9  refresh_token/expires_in 断言
  tests/.../test_local_auth_migration.py     +2  迁移测试调整

测试: 1138/1139 pass (1 预存 health route 失败, 与本次无关)
```

---

## 裁决: APPROVED

R3 修复在 R2 极简架构基础上添加了:
1. 无状态 JWT refresh token (无存储层, 无新基础设施)
2. 2 个密码策略环境变量 (无策略模式, 无过度抽象)
3. 事务提交修复 (纯缺陷修复)
4. 前端健壮性改进 (超时/重试, 服务端无变更)

**全部变更在现有架构边界内, 过度设计评分维持 2/10。可合并。**
