# Security Audit Report: 企业登录功能

**审核日期**: 2026-06-18
**审核范围**: 企业登录功能 (bcrypt / JWT / 速率限制 / 密码策略 / XSS-CSRF)
**审核员**: Security Reviewer (Hermes Agent)
**代码库**: D:\Programs\RAG-Local-System

---

## 裁决: CRITICAL — REJECT (2 个 CRITICAL 发现)

---

## CRITICAL (必须修复才能合并)

### C1: 用户/组管理端点无认证保护 (CWE-306: Missing Authentication)

**影响文件**:
- `apps/api/routes/users.py` (POST /users, GET /users)
- `apps/api/routes/groups.py` (CRUD /groups)

**漏洞详情**: 
`POST /users`、`GET /users`、`GET /groups`、`POST /groups`、`PUT /groups/{id}`、`DELETE /groups/{id}` 这些端点仅使用了 `RequestContextDep`（请求上下文），没有使用 `AuthContextDep` 或 `AuthenticatedRequestContextDep` 进行身份验证。**任何未认证的攻击者都可以**:
- 列出所有用户 (含用户名、邮箱、显示名)
- 创建新用户 (含任意密码)
- 查看/修改/删除所有用户组

**当前代码** (`apps/api/routes/users.py:48`):
```python
@router.post("", response_model=ApiResponse[UserResponse])
async def create_user(
    body: CreateUserRequest,
    context: RequestContextDep,          # ← 仅有请求上下文
    service: UserServiceDep,
) -> ApiResponse[UserResponse]:
```

**修复方案**: 在所有用户/组管理端点添加认证依赖:
```python
from apps.api.dependencies import AuthenticatedRequestContextDep

@router.post("", response_model=ApiResponse[UserResponse])
async def create_user(
    body: CreateUserRequest,
    context: RequestContextDep,
    auth: AuthenticatedRequestContextDep,  # ← 添加认证
    service: UserServiceDep,
) -> ApiResponse[UserResponse]:
```
同时建议添加权限检查 (如 `admin:settings` 权限), 确保只有管理员可以管理用户。

**OWASP 参考**: [A01:2021 Broken Access Control](https://owasp.org/Top10/A01_2021-Broken_Access_Control/)

---

### C2: 无密码策略 — 接受空密码和弱密码 (CWE-521: Weak Password Requirements)

**影响文件**:
- `apps/api/routes/users.py:20` — `CreateUserRequest.password`
- `apps/api/routes/auth.py:20` — `LoginRequest.password`
- `packages/auth/user_service.py:29` — `create_user()` 无密码验证

**漏洞详情**: 
密码字段定义为 `Field(..., min_length=1, max_length=255)`, 即最小长度为 1。这意味着以下密码都可以通过:
- 空字符串经 `strip()` 后的单字符
- `"a"`, `"1"` 等极弱密码
- `packages/auth/user_service.py:create_user()` 完全没有密码强度验证

**当前代码**:
```python
# apps/api/routes/users.py:19-20
class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)  # ← min_length=1 太弱!

# packages/auth/user_service.py:34
password_hash = bcrypt.hashpw(
    password.encode("utf-8"),
    bcrypt.gensalt(),
).decode("utf-8")
# ← 没有任何密码强度检查
```

**修复方案**: 
1. 设置最小密码长度为 8-12 个字符
2. 添加复杂度要求 (至少包含大写、小写、数字、特殊字符中的 3 类)
3. 在 `UserService.create_user()` 中添加密码验证:

```python
# packages/auth/user_service.py
import re

PASSWORD_MIN_LENGTH = 8
PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)|"
    r"(?=.*[a-z])(?=.*[A-Z])(?=.*[^a-zA-Z\d])|"
    r"(?=.*[a-z])(?=.*\d)(?=.*[^a-zA-Z\d])|"
    r"(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z\d])"
)

def _validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise DomainError(
            code="AUTH_WEAK_PASSWORD",
            message=f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
            status_code=422,
        )
    if not PASSWORD_PATTERN.search(password):
        raise DomainError(
            code="AUTH_WEAK_PASSWORD",
            message="Password must contain at least 3 of: uppercase, lowercase, digit, special character.",
            status_code=422,
        )
```

**OWASP 参考**: [A04:2021 Insecure Design](https://owasp.org/Top10/A04_2021-Insecure_Design/)

---

## HIGH (应在本 PR 中修复)

### H1: 登录端点无专用速率限制 (CWE-307: Improper Restriction of Excessive Authentication Attempts)

**影响文件**: `apps/api/rate_limit_middleware.py`, `apps/api/main.py`

**漏洞详情**: 
全局速率限制器 (100 请求/60秒) 对所有端点一视同仁。登录端点 `/auth/login` 与普通 API 调用共享同一个令牌桶。攻击者可以每分钟发起 100 次登录尝试。考虑到默认 burst_multiplier=2, 实际初始可用 tokens 为 200, 首次突发可达 200 次登录尝试。

**当前代码** (`apps/api/main.py:42-43`):
```python
rate_limit_config = _rate_limit_config(settings)
app.add_middleware(RateLimitMiddleware, config=rate_limit_config)
# 全局速率限制: 100 req/60s, 对所有路径均等
```

**修复方案**: 为登录端点添加独立、更严格的速率限制 (如 5 次/分钟/IP):

```python
# 方案 A: 在中间件中按路径区分
class RateLimitMiddleware(BaseHTTPMiddleware):
    _LOGIN_PATH = "/auth/login"
    
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == self._LOGIN_PATH:
            cost = self._config.login_cost or 20  # 每次登录消耗 20 tokens
        else:
            cost = 1
        if not await self._limiter.is_allowed(client_ip, cost=cost):
            ...

# 方案 B: FastAPI 依赖注入级速率限制
from slowapi import Limiter
limiter = Limiter(key_func=lambda: "global")

@router.post("/login")
@limiter.limit("5/minute")  # ← 登录端点专用限制
async def login(...):
```

**附**: 还应添加帐户锁定机制 — 连续 N 次失败登录后锁定帐户一段时间。

**OWASP 参考**: [WSTG-ATHN-02](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/04-Authentication_Testing/02-Testing_for_Default_Credentials)

---

### H2: 种子数据包含硬编码弱密码 (CWE-798: Use of Hard-coded Credentials)

**影响文件**: `packages/auth/seed.py:32-36`

**漏洞详情**: 
种子数据中硬编码了可猜测的密码:

```python
SEED_USERS: list[dict[str, str]] = [
    {"username": "admin", "password": "admin123", ...},      # ← 极弱密码
    {"username": "editor1", "password": "editor123", ...},    # ← 极弱密码
    {"username": "editor2", "password": "editor123", ...},    # ← 重复密码
    {"username": "viewer1", "password": "viewer123", ...},    # ← 极弱密码
    {"username": "viewer2", "password": "viewer123", ...},    # ← 重复密码
]
```

如果此脚本在生产环境中运行 (或生产数据库从种子数据初始化), 管理员帐户立即可被攻破。

**修复方案**:
1. 种子密码应从环境变量读取 (如 `SEED_ADMIN_PASSWORD`)
2. 或使用 `secrets.token_urlsafe(16)` 生成随机密码并打印到 stdout
3. 在 `seed.py` 顶部添加醒目的 WARNING 注释

```python
import secrets

SEED_USERS = [
    {
        "username": "admin",
        "password": os.getenv("SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(16),
        ...
    },
]
```

**OWASP 参考**: [A07:2021 Identification and Authentication Failures](https://owasp.org/Top10/A07_2021-Identification_and_Authentication_Failures/)

---

## MEDIUM (应记录到 Backlog)

### M1: 未配置 CORS 策略

**影响文件**: `apps/api/main.py`

**漏洞详情**: FastAPI 应用未配置 `CORSMiddleware`。虽然默认行为在不同部署场景中有差异, 但显式配置 RESTRICTIVE CORS 是最佳实践。

**修复方案**:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # ← 显式白名单
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### M2: 前后端登录响应格式不匹配

**影响文件**: `apps/web/src/lib/auth.ts:244-263`

**漏洞详情**: 前端 `loginUser()` 期望从 API 响应 JSON 的顶层获取 `user_id`、`roles`、`permissions` 等字段, 但后端返回的是 `ApiResponse[LoginResponseData]`, 其中数据被包裹在 `data` 字段内, 且 `LoginResponseData` 仅有 `access_token` 和 `token_type`。JWT 中的 claims (user_id、roles 等) 没有作为响应字段返回。

```
后端返回: { request_id, data: { access_token, token_type } }
前端期望: { user_id, roles, permissions, access_token }
```

这导致前端从 JWT 令牌中获取不到用户角色和权限信息, 可能影响授权检查。

**修复方案**: 在 `auth.py` 的 `login` 端点响应中返回 JWT claims, 或在 `auth.ts` 中解析 JWT payload (注意: 仅用于显示, 安全验证应在后端进行)。

### M3: JWT 密钥管理无轮换机制

**影响文件**: `packages/auth/parsers.py:23-38`

**漏洞详情**: JWT 签名密钥仅从单一环境变量 `JWT_SECRET` 读取, 无密钥 ID (`kid`)、无密钥轮换策略。若密钥泄露, 无法单独撤销特定密钥签发的令牌。

**修复方案**: 考虑使用 JWKS (JSON Web Key Set) 或至少支持多个密钥并按 `kid` 选择。

---

## LOW (建议强化)

### L1: bcrypt 输出长度与列定义不一致

`LocalUserModel.password_hash` 定义为 `String(255)`, 但 bcrypt 哈希值固定为 60 字符。这不会引起功能问题, 但 `String(60)` 更加精确。

### L2: JWT 缺少 `jti` 声明

当前 JWT claims 不含 `jti` (JWT ID), 无法实现令牌撤销/黑名单功能。

### L3: JWT 缺少 `nbf` 声明

当前 JWT 使用 `iat` + `exp` 但无 `nbf` (Not Before)。某些场景下 `nbf` 可用于延迟令牌生效。

---

## 合规项 (已正确实施)

| 检查项 | 状态 | 说明 |
|--------|------|------|
| bcrypt 算法 | ✅ | `bcrypt>=4.0`, 默认 12 rounds, 安全 |
| bcrypt.checkpw | ✅ | 使用恒定时间比较 |
| 密码存储 | ✅ | 仅存储 bcrypt 哈希, 无明文 |
| JWT 算法混淆防护 | ✅ | `decode_jwt_token` 仅接受配置的算法 `algorithms=[settings.algorithm]` |
| JWT exp 强制验证 | ✅ | `options={"require": ["exp"]}`, PyJWT 默认验证 |
| JWT 密钥验证 | ✅ | 签发和验证前检查 `settings.secret` 是否存在 |
| 帐户枚举防护 | ✅ | 用户不存在和密码错误返回相同错误消息和状态码 |
| SQL 注入防护 | ✅ | SQLAlchemy 参数化查询, 无字符串拼接 |
| 密码输入类型 | ✅ | 前端使用 `type="password"` |
| 前端 autoComplete | ✅ | `autoComplete="username"` / `autoComplete="current-password"` |
| 不活跃用户拦截 | ✅ | `login()` 检查 `is_active`, 返回 403 |
| X-Request-ID 追踪 | ✅ | 所有响应包含 request_id |
| 速率限制器架构 | ✅ | Token bucket 设计良好, 包含陈旧桶清理 |

---

## 总结

| 严重程度 | 数量 | 关键项 |
|----------|------|--------|
| CRITICAL | 2 | 无认证的用户/组管理端点, 无密码策略 |
| HIGH | 2 | 登录端点无专用速率限制, 硬编码弱种子密码 |
| MEDIUM | 3 | 无 CORS 配置, 前后端响应不匹配, 无密钥轮换 |
| LOW | 3 | 列长度不精确, 缺少 jti/nbf |

**结论**: 存在 2 个 CRITICAL 级别漏洞 — 根据审查规则, **自动 REJECT**。必须先修复 C1 和 C2 后重新提交审核。
