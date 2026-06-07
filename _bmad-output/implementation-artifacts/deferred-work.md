## Deferred from: code review of 1-3-requestcontext-与-authcontext-注入.md (2026-05-27)

- 完整 tenant membership 校验需要用户/租户/角色持久化与 RBAC 数据源，本 story 只能消费已验证 token/header 中的认证事实。后续数据库治理和 RBAC story 应补充 membership 校验边界。
- `AccessFilter` 还没有表达 public access、deny rule、role intersection、继承等完整 ACL 语义；这是后续 retrieval/tool policy 的设计工作，本 story 只提供基础结构。
- JWT 生产级硬化还包括 issuer/audience 是否强制、token purpose/type、algorithm allowlist、JWKS/key rotation 和 auth readiness gate；当前 story 未定义这些策略边界。
- `X-Request-ID` / `X-Trace-ID` 的最大长度、字符集和日志清洗策略尚未定义；空白 header 需要本 story 修复，完整日志安全策略可纳入 Story 1.4。
- 401 响应未设置 `WWW-Authenticate`，会影响部分 OAuth/Bearer 客户端互操作；当前 envelope 行为满足 story，标准 header 可在认证接口稳定化时补齐。

## Deferred from: code review of 2-2-parser-协议与-markdown-txt-解析.md (2026-06-04)

- PDF/DOCX uploads are accepted before PDF/DOCX parsers exist (`packages/data/service.py:47`); Story 2.3 owns PDF/DOCX parser support.
- Enqueued jobs store `queue_job_id` but never move to an explicit `queued` status (`packages/data/storage/repositories.py:56`); queue lifecycle normalization spans Story 2.1/worker operations.
- Upload object cleanup can mask the original metadata write failure (`packages/data/service.py:245`); upload compensation hardening belongs to upload service follow-up.
- Normal document listing does not exclude soft-deleted documents (`packages/data/storage/repositories.py:143`); soft-delete query policy is a later document lifecycle concern.
- Restricted ACL can be accepted without any principals (`packages/data/service.py:541`); full ACL semantics belong to RBAC/retrieval policy work.

## Deferred from: code review of 2-6-chunk-metadata-contract-与持久化.md (2026-06-06)

- `create_upload_records` lacks cross-record tenant/document/version consistency checks (`packages/data/storage/repositories.py:29`); this is a real storage governance risk, but it predates Story 2.6 and should be handled with a broader repository/database consistency hardening pass.

## Deferred from: code review of 4-5-sse-streaming-回答事件.md (2026-06-07)

- Citation `source_uri` is passed through `Citation.from_source()` and can expose local filesystem paths in both existing non-streaming query citations and the new SSE citation events. This needs a cross-module source metadata policy instead of a streaming-only patch.
