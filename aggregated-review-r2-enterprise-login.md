# 聚合审查报告 R2：企业登录功能（Round 2）

**聚合日期**: 2026-06-18
**审查员**: Review Aggregator (Hermes Agent)
**输入来源**:
- Code Reviewer (t_85a49e09) — 代码质量审查 R2
- Security Reviewer (t_8a927b89) — 安全审计 R2
- Architecture Reviewer (t_e00db1a7) — 架构审查 R2

---

## Review Score: 45/100

> 计算: 100 - 1×P0(20) - 2×P1(10) - 5×P2(3) = 100 - 20 - 20 - 15 = 45
> **Score < 90 → 自动路由至 Fix Agent 进行 Round 3 返工。**

---

## 裁决: NEEDS_CHANGES

Round 1 的 4 个 P0 阻断和 5 个 P1 缺陷**全部已修复验证通过**（三位审查员一致确认）。但 Round 2 复审发现 **1 个新的 P0 数据丢失缺陷**（API 写入未 commit）和 **2 个 P1 功能缺陷**（迁移缺少关键列、JWT 无刷新机制）。

---

## P0 — Blocker (必须修复才能合并)

### [P0-1] API 数据库写入后未 commit，导致事务回滚、数据丢失
**来源**: Code Reviewer (CRITICAL finding)

**影响文件**:
- `packages/auth/user_service.py:52` — create_user() 执行 `session.flush()` 但未 `commit()`
- `packages/auth/group_service.py:32` — create/update/delete 组操作均未 commit

**问题**: `UserService.create_user()` 和 `GroupService` 的全部写操作仅调用 `session.flush()`（将变更刷入数据库会话但未持久化），缺少 `session.commit()`。当请求结束、会话关闭时，未提交的事务自动回滚，导致用户/组创建操作实际上不持久化。

**后果**: 
- 通过 API 创建的用户/组在请求返回后丢失（数据库无记录）
- 种子数据脚本 (`seed.py`) 可能使用不同的提交路径，掩盖了 API 路径的问题
- 28 个测试通过但缺少端到端 DB 写入验证测试

**修复方案**:
1. 在 `user_service.py` 和 `group_service.py` 的所有写操作末尾添加 `session.commit()`
2. 添加端到端测试验证 API 写入后数据库确实有记录
3. 审查所有服务层写操作，确保事务正确提交或回滚

---

## P1 — Must Fix (应在合并前修复)

### [P1-1] 迁移脚本 user_groups 表缺少 roles 和 permissions 列
**来源**: Code Reviewer (MAJOR) + Security Reviewer (F1, HIGH) + Architecture Reviewer (F-1, medium)

**影响文件**: `migrations/versions/20260618_0014_local_users_and_groups.py:23`

**问题**: `UserGroupModel` 在 `packages/auth/models.py` 中定义了 `roles` (JSON) 和 `permissions` (JSON) 列，但 Alembic 迁移脚本仅创建了 `id`、`name`、`description` 列，未包含 `roles` 和 `permissions`。这意味着 ORM 模型与数据库 schema 不一致。

**后果**:
- 任何依赖 `roles`/`permissions` 列的操作（如组的授权检查）将在运行时失败
- 三位审查员独立发现此问题，均标记为 HIGH/MAJOR 严重性

**修复方案**: 在迁移脚本中添加 `roles` 和 `permissions` 列定义，或创建新的迁移补充这两列。

---

### [P1-2] JWT 24 小时固定过期 + 无 Refresh Token 机制
**来源**: Code Reviewer (MAJOR)

**影响文件**: `packages/auth/login_service.py:92`

**问题**: JWT 固定 24 小时过期 (`exp = int(time.time()) + 86400`)，且系统完全没有 Refresh Token 机制。用户每次过期后必须重新输入凭据登录。

**后果**:
- 用户体验差：每天必须重新登录
- 安全权衡失当：24h 过期对活跃用户过短，对已泄露令牌又过长（无撤销手段）
- 缺乏标准的企业认证流程（access_token + refresh_token 双令牌模式）

**修复方案**:
1. 引入短有效期 access_token（如 15-60 分钟）+ 长有效期 refresh_token（如 7-30 天）
2. 或至少使 access_token 过期时间可配置（环境变量 `JWT_EXPIRY_SECONDS`）
3. 添加 `/auth/refresh` 端点用于令牌刷新

---

## P2 — Should Fix

### [P2-1] LoginResponseData.tenant_id 类型与 LoginResult 不一致
**来源**: Code Reviewer (minor)

**影响文件**: `apps/api/routes/auth.py:28`

**问题**: `LoginResponseData.tenant_id` 字段类型定义与 `LoginResult` 中的 `tenant_id` 不一致（如 `str` vs `Optional[str]`）。

**修复方案**: 统一两者类型定义，确保 `LoginResponseData` 与 `LoginResult` 完全匹配。

---

### [P2-2] 前端 fetch 无超时/重试机制
**来源**: Code Reviewer (minor)

**影响文件**: `apps/web/src/lib/auth.ts:235`

**问题**: `loginUser()` 中的 `fetch()` 调用未设置超时 (`AbortController` / `signal`) 和重试逻辑。网络故障时请求可能无限挂起。

**修复方案**: 添加 `AbortController` 超时（如 30 秒）和指数退避重试（最多 3 次）。

---

### [P2-3] JWT claims 使用 list 但 LoginResult 使用 tuple，类型不一致
**来源**: Code Reviewer (minor)

**影响文件**: `packages/auth/login_service.py:88`

**问题**: JWT payload 中的 `roles`/`permissions` 以 list 存储，但 `LoginResult` 期望 tuple 类型，类型签名不一致。

**修复方案**: 统一为一种集合类型（建议 list，与 JSON 序列化兼容）。

---

### [P2-4] 特殊字符验证正则 `[^A-Za-z0-9]` 过于宽泛
**来源**: Code Reviewer (minor)

**影响文件**: `packages/auth/user_service.py:79`

**问题**: 用户名字符白名单使用排除式正则 `[^A-Za-z0-9]`，排除了所有非字母数字字符，包括下划线 `_`、连字符 `-`、点 `.` 等常见的用户名合法字符。应使用包含式正则明确允许的字符集。

**修复方案**: 改为明确的正向字符集，如 `^[A-Za-z0-9._-]+$`，或根据业务需求定义允许的字符范围。

---

### [P2-5] 密码策略验证逻辑硬编码在服务层
**来源**: Architecture Reviewer (F-2, low)

**影响文件**: `packages/auth/user_service.py`

**问题**: 密码复杂度验证逻辑直接硬编码在 `UserService` 中，缺乏配置化。若需调整策略（如企业要求不同强度），需要修改代码重新部署。

**修复方案**: 将密码策略参数（最小长度、字符类别要求等）提取为配置常量或环境变量，或引入策略模式。

---

## P3 — Nice to Have (不扣分，记录到 Backlog)

| 编号 | 建议 | 来源 |
|------|------|------|
| P3-1 | Groups API 响应不暴露 roles/permissions 字段 | Architecture Reviewer (F-3) |
| P3-2 | `TENANT_ID` 常量拼写不统一（部分文件用 `TENANT_ID`，部分用 `tenant_id`） | Architecture Reviewer (F-4) |
| P3-3 | 添加 DB 写入端到端测试（验证 API 写入后数据库确实有记录） | Code Reviewer |

---

## 冲突解决

### 冲突 1: 总体裁决不一致
- **Security Reviewer**: 裁决 **PASS** — Round 1 的 7 个安全漏洞全部修复，无新的安全漏洞
- **Code Reviewer**: 裁决 **needs_changes** — 发现 CRITICAL（事务未提交）和 2 个 MAJOR 功能缺陷
- **Architecture Reviewer**: 审查通过，极简合理

→ **裁决: NEEDS_CHANGES**。Security Reviewer 从安全审计视角给出 PASS（无安全漏洞）是合理的；Code Reviewer 发现的功能缺陷（事务回滚、迁移缺列）属于代码质量和功能正确性问题，不是安全审计范围。两者的判断不矛盾，但功能缺陷（尤其是 P0 数据丢失）必须修复才能合并。Architecture Reviewer 侧重架构合理性，不涉及功能验证。

### 冲突 2: migration 缺列严重性评估
- Code Reviewer: **MAJOR**
- Security Reviewer: **HIGH** (functional bug)
- Architecture Reviewer: **medium**

→ **裁决: P1 (MUST FIX)**。三位审查员一致认定此问题存在且需要修复。严重性方面，Code 和 Security 认为 HIGH/MAJOR，Architecture 偏向 medium（仅影响 schema 而不影响当前运行时因为它依赖的 API 路径尚未使用这些列）。综合判定为 P1：功能缺陷，当前影响有限但随着 roles/permissions 功能的完善会变成严重问题。

---

## 合规项 (已正确修复 — Round 1 全部通过)

| Round 1 缺陷 | 状态 | 确认人 |
|-------------|------|--------|
| P0-1 前后端 API 契约不匹配 | ✅ 已修复 | Code + Security + Arch |
| P0-2 用户/组端点无认证 (CWE-306) | ✅ 已修复 | Security + Code |
| P0-3 无密码策略 (CWE-521) | ✅ 已修复 | Security + Code |
| P0-4 JWT 缺少 roles/permissions | ✅ 已修复 | Security + Code |
| P1-1 种子数据硬编码弱密码 | ✅ 已修复 | Security |
| P1-2 登录无专用速率限制 | ✅ 已修复 | Security |
| P1-3 硬编码 Tenant ID | ✅ 已修复 | Security |
| P1-4 用户名枚举泄露 | ✅ 已修复 | Security |
| P1-5 错误码泄露账户状态 | ✅ 已修复 | Security |

**28/28 测试通过** (Code Reviewer 确认)

---

## 统计汇总

| 严重程度 | 数量 | 来源 |
|----------|------|------|
| P0 — Blocker | 1 | Code(1) |
| P1 — Must Fix | 2 | Code(2) + Security(1) + Arch(1) |
| P2 — Should Fix | 5 | Code(4) + Arch(1) |
| P3 — Nice to Have | 3 | Code(1) + Arch(2) |
| 合并去重前总发现数 | **13** | Code(8) + Security(1 new) + Arch(4) |
| **合并去重后独立发现** | **11** | 1 项被三方共同标记（migration缺列） |

**去重合并项**:
- migration 缺 roles/permissions 列 → Code MAJOR + Security F1-HIGH + Arch F-1-medium → P1-1

---

## 下一步

Score = 45/100 < 90 → **自动创建 Fix Agent 任务进行 Round 3 返工。**
