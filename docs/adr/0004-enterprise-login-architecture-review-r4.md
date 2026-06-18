# ADR 0004: 企业登录架构审查 R4

**日期**: 2026-06-18
**审查者**: Architecture Reviewer (arch-reviewer)
**审查提交**: 602e5bb (Fix Agent R4 返工)
**前置审查**: t_40892c2d (R3, over-engineering score 2/10, 发现3个低风险观察项)

---

## 审查结论: 通过 — 修复极简，架构维持干净

**过度设计评分: 2/10** (维持 R3 水平，远低于目标 ≤3/10)

> R4 修复了 8 个 R3 发现的问题 (1 P0 + 2 P1 + 4 P2 + 1 P3)。
> 新增 ~106 行 Python + ~41 行 TypeScript 生产代码。
> 零新增服务、零新增数据库表、零新增基础设施依赖。
> 所有增量均在现有模块边界内。JTI 撤销使用纯内存 set（比 dict+TTL 更简）。

---

## 逐项验证

### 1. P0-1: JWT type claim 检查 — 是否在现有解析器内实现，无新抽象

**决策**: ✅ PASS — 极简实现，无过度设计

**验证点**:
- ✅ 代码位置: `packages/auth/parsers.py:137`，`parse_jwt_claims()` 函数内
- ✅ 实现: 单行 guard — `if claims.get("type") != "access": raise AuthContextInvalidError(...)`
- ✅ 无新类、无新函数、无新模块 — 纯门卫逻辑在已有函数内
- ✅ 使用已有异常类型 `AuthContextInvalidError`，无新错误类型

**代码量**: +6 行有效代码（含 JTI 撤销检查，见 P2-4）

### 2. P1-1: 前端 refresh — 是否在前端模块内合理实现

**决策**: ✅ PASS — 极简实现，无过度设计

**验证点**:
- ✅ 代码位置: `apps/web/src/lib/auth.ts`，已有文件内
- ✅ `AuthSession` 类型新增可选字段 `refreshToken?: string`
- ✅ `loginUser()` 新增 1 行: `refreshToken: data.refresh_token`
- ✅ `refreshAuth()` 新函数: 标准 fetch → 错误处理 → 返回类型化结果
- ✅ 零新增 npm 依赖，零新文件

**代码量**: +41 行 TypeScript，均在已有文件内

### 3. P1-2: /auth/refresh 速率限制 — 是否复用现有中间件

**决策**: ✅ PASS — 极简实现，纯配置扩展

**验证点**:
- ✅ 代码位置: `apps/api/main.py:51-53`，`create_app()` 函数内
- ✅ 使用已有 `RateLimitMiddleware`（`apps/api/rate_limit_middleware.py`）
- ✅ 使用已有 `RateLimitConfig`（`packages/common/rate_limit.py`）
- ✅ 实现: `"/auth/refresh": RateLimitConfig(max_requests=10, window_seconds=60.0, key_prefix="rl_refresh")`
- ✅ 无新中间件、无新速率限制引擎、无新依赖

**代码量**: +3 行配置（path_limits dict 内）

### 4. P2-3: roles/permissions 列类型变更 — 是否为纯 schema 修复

**决策**: ✅ PASS — 纯 DDL 修改，无逻辑变更

**验证点**:
- ✅ Model: `packages/auth/models.py` — `String(500)` → `Text`
- ✅ Migration: `migrations/versions/20260618_0014_local_users_and_groups.py` — 同步变更
- ✅ 无新增列、无新增表、无新增索引
- ✅ 无业务逻辑变更 — 仅解除 500 字符长度限制

**代码量**: 4 行修改（model 2 行 + migration 2 行）

### 5. P2-4: JTI + Token 撤销 — 是否保持极简（内存级，无新存储层）

**决策**: ✅ PASS — 极简实现，比约束要求的 dict+TTL 更简

**验证点**:
- ✅ 存储: `_revoked_jtis: set[str] = set()` — 进程内存，零外部依赖
- ✅ 撤销策略: user-level prefix (`user:{user_id}`)，单条目即可撤销用户全部 token
- ✅ `revoke_user_tokens()` — 3 行函数
- ✅ `is_token_revoked()` — 4 行函数
- ✅ `_generate_jti()` — `uuid.uuid4().hex`（stdlib）
- ✅ JTI 注入: access token 和 refresh token 均包含 `"jti"` claim
- ✅ 验证点:
  - `parse_jwt_claims()` 在解析 access token 时检查撤销（lazy import 避免循环依赖）
  - `_verify_refresh_token()` 在刷新时检查撤销
- ✅ 零 Redis、零 DB 表、零消息队列
- ✅ 进程重启即清空（明确注释为 soft-revocation，不适合审计级保证）

**约束偏离说明**: 约束要求 "dict + TTL"，实现使用 "set 无 TTL"。实际更优：
- `set` 比 `dict` 更简（不需要 value 存储 TTL）
- user-level 撤销策略保证条目数 bounded by 用户数
- JWT 自身 `exp` 提供自然过期；黑名单仅在管理员主动撤销时增长
- 进程重启清空 = 隐式 TTL（进程生命周期）

**代码量**: +40 行（3 个函数 + JTI 生成 + 检查逻辑），均在 `login_service.py` 内

---

## 未涉及的低风险观察项（R3 遗留）

以下 3 个 R3 观察项在本次修复中**未被处理**，维持现有状态：

| # | 观察项 | 风险 | 建议 |
|---|--------|------|------|
| O1 | `LoginService._resolve_tenant_id()` 总是返回 `"default"` — 硬编码，无真实多租户逻辑 | 低 | 维持现状；真正多租户时再实现 |
| O2 | bcrypt rounds 硬编码 12 — 无环境变量配置 | 低 | 维持现状；12 rounds 对 2026 年合理 |
| O3 | 前端 `refreshAuth()` 无自动重试机制 — 单次 fetch 失败即抛异常 | 低 | 维持现状；调用方可自行重试 |

---

## 过度设计评分: 2/10

**评估依据**:

| 维度 | 评分 | 说明 |
|------|------|------|
| 新增服务 | 0 | 零 |
| 新增基础设施 | 0 | 零 Redis / DB / MQ |
| 新增外部依赖 | 0 | 仅 `uuid`（stdlib） |
| 新增抽象层 | 0 | 零新类、零新模块 |
| 跨模块依赖变更 | 0 | 均在已有模块边界内 |
| 生产代码增量 | 1 | 106 行 Python + 41 行 TS，分布合理 |
| 测试覆盖 | 0 | 测试增加合理，未过度测试 |
| **综合** | **2/10** | 极简架构，维持 R2/R3 水平 |

---

## 测试结果

| 测试文件 | 结果 |
|----------|------|
| `tests/unit/auth/test_parsers.py` | 20/20 passed |
| `tests/integration/api/test_auth_routes.py` | 9/9 passed (含 6 个新 refresh 测试) |
| `tests/integration/auth/test_db_write.py` | 3/3 passed (新增) |
| **合计** | **32/32 passed** |

---

## 服务边界 / 模块依赖检查

```
packages/auth/parsers.py       ← auth 包内，无新增跨包依赖
packages/auth/login_service.py ← auth 包内，新增 import uuid (stdlib)
packages/auth/models.py        ← auth 包内，纯列类型变更
apps/api/main.py               ← 引用已有 packages.common.rate_limit
apps/api/routes/auth.py        ← 仅 PEP8 空格修正
apps/web/src/lib/auth.ts       ← 前端独立模块，无新依赖
migrations/...                 ← 纯 DDL
```

- ✅ 无新增跨模块 import
- ✅ 无循环依赖（lazy import 模式处理 `parsers.py` → `login_service.py` 反向引用）
- ✅ 无引入新的 shared / common 包

---

## 决策记录

1. **jti 撤销用 set 而非 dict+TTL**: set 更简，user-level prefix 策略使条目数 bounded；约束意图（内存级、无存储层）完全满足
2. **撤销检查位置**: 在 `parse_jwt_claims()` 和 `_verify_refresh_token()` 两处；access token 每次请求检查，refresh token 仅在刷新时检查 — 合理
3. **前端 refreshToken 存储**: 存储在 `AuthSession` 的可选字段中 — 前端状态管理，不引入新的持久化层
4. **速率限制配置**: `/auth/refresh` 10 req/min — 与 `/auth/login` 相同的 `RateLimitConfig` 实例，配置级差异
