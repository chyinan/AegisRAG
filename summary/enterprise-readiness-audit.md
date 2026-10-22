# AegisRAG 企业级投产能力审查摘要

> 审查日期：2026-08-30

## 总结

AegisRAG 已明显超过普通“上传文件 + 向量检索 + Prompt”的 RAG 项目，真实强项是权限前置、授权 citation、可解释检索诊断、治理审阅与安全 Tool Registry。当前适合做受控单租户试点和高质量作品集，但尚不适合宣称共享多租户企业 SaaS 已可投产。

## 已确认强项

- tenant/user/ACL 已贯穿文档、chunk、vector、retrieval log、memory、agent run、tool call 与 audit log。
- pgvector 与 PostgreSQL FTS 查询在数据库查询阶段限定 tenant、状态、软删除与 ACL。
- Dense + Sparse + RRF + rerank + threshold + context packing + citation allowlist 已形成真实主链。
- Prompt builder 将用户、历史和文档标记为不可信数据，并检测常见注入模式。
- Tool Registry 实现 schema、permission、rate limit、timeout、audit 与持久化。
- 后端完整测试：1290 passed、1 skipped、5 xpassed，总覆盖率 84%；Ruff 通过。
- Web TypeScript、27 个 Vitest 测试和 Next production build 通过。

## 关键缺口

- 本地身份体系与 tenants/users/roles 治理模型未打通；local user 无 tenant_id，登录 tenant 来自全局环境变量。
- 没有 PostgreSQL RLS；多租户隔离依赖应用查询显式携带 tenant_id。
- token revocation、HTTP rate limit、tool rate limit 均为进程内状态，不适合多副本。
- Sparse 实际为 PostgreSQL FTS `ts_rank_cd`，不是 README 所称 BM25。
- Dense/Sparse 串行执行，任一分支失败会让 Hybrid 整体失败。
- Adaptive Routing 与 Graph RAG 有孤立实现但未接入默认应用装配。
- LLM reranker 读取不存在的 `GenerateResponse.content`，真实 provider 会进入降级；正确字段为 `text`。
- 生产 Agent API 使用 deterministic stub stepper，固定返回答案，不是实际 LLM ReAct Agent。
- Extended eval 的 220 个案例中有 189 条占位语料，且未接 CI；CI smoke 使用 Fake LLM。
- strict mypy 有 93 个错误；Web ESLint 有 6 个错误；CI 未运行 mypy 或任何 Web 检查。
- Graph RAG、Milvus adapter、RAGAS evaluator 在完整测试覆盖率报告中均为 0%。
- ingestion 缺少企业连接器、增量同步、源端删除/ACL 传播、magic bytes、恶意文件扫描、隔离区与 DLP/PII 分类。
- RQ 缺少显式 job timeout/retry/TTL/DLQ/backlog 治理。
- Helm/Compose 缺少生产级外部密钥、NetworkPolicy、HPA/PDB、Ingress TLS、备份恢复与托管依赖边界；Compose 还有开发机绝对路径和匿名 Grafana。
- Prometheus 当前以 HTTP 通用指标为主，缺少 retrieval、quality、provider、queue、tenant cost/SLO 等业务指标。

## 建议定位

不要主打“功能最多的 RAG”，应主打：

> 可审计、可解释、可回归、权限前置的企业可信 RAG / EvidenceOps 平台。

## 优先路线

实施依赖已经复核，固定为七个阶段：

1. 统一租户身份与 PostgreSQL RLS/复合租户外键。
2. 将 token revocation、session 与 HTTP/tool rate limit 迁移到 Redis，再接 OIDC/JWKS。
3. 修复真实 reranker，版本化 retrieval method，并行 Dense/Sparse 与单分支降级。
4. 稳定 canonical ACL/source identity 后，完成文档安全接入与一个企业连接器。
5. 在身份和检索契约稳定后接通真实 structured-output AgentStepper。
6. 闭合 Review Queue → 版本化 Eval Dataset → CI/Nightly Gate 与业务 SLO。
7. 补齐 CI、镜像安全、Kubernetes 安全/弹性、备份恢复和故障演练。

不得颠倒的核心契约是：`TenantPrincipal = tenant_id + user_id + identity_provider + external_subject`；RLS 只能通过事务级 `SET LOCAL` 注入上下文；canonical ACL/source identity 和 retrieval/citation DTO 必须先版本化再扩展连接器与 Agent。

### P0：把已有主链变成可信可投产证据

1. 统一 tenant identity，完成 RLS、复合租户外键和跨租户负向 E2E。
2. 修复真实 reranker，并建立真实 provider contract/nightly tests。
3. 将 PostgreSQL FTS 如实命名，或引入真正 BM25；并行两路检索并支持单分支降级。
4. 接入真实 LLM Agent stepper，或暂时把 Agent 宣传降级为 governed runtime foundation。
5. 清零 mypy/ESLint，CI 加入 Web、mypy、真实 PostgreSQL/Redis/MinIO E2E。
6. 清理 eval 占位语料，区分 deterministic safety gate 与真实模型质量 gate。
7. 移除默认密码、匿名 Grafana和绝对路径，补 Secret/backup/HA 安全基线。

### P1：形成招聘市场上的稀缺闭环

1. EvidenceOps：低置信/无答案/伪造 citation 自动进入 review queue，人工裁决转 eval case，CI 回归阻止质量下降。
2. 企业连接器选择一个做深（SharePoint 或 Confluence），实现增量同步、版本、删除和 ACL 传播。
3. 文档安全接入：magic/MIME、ClamAV、quarantine、PII/DLP、保留策略。
4. OIDC/JWKS/SSO + group claim 映射；需要 SaaS 再补 SCIM、tenant quota/cost budget。
5. Retrieval Diagnostics 增加 dense/sparse/RRF/rerank 对照、降级原因、质量与延迟 SLO。

### P2：有证据后再做

- Milvus、Graph RAG、多 Agent、通用 Web crawler、多向量库矩阵。

这些功能只有在真实数据规模、质量对照和运维边界证明收益后才值得加入主线。

## 推荐演示

1. 两个角色问同一问题，展示不同授权结果以及跨租户 chunk 未进入候选集。
2. 上传带 prompt injection 的文档，展示风险标记、系统规则不被覆盖和安全 citation。
3. 从答案点击 evidence，打开 retrieval timeline，解释 dense/sparse/RRF/rerank/threshold。
4. 将错误 citation 或 no-answer 送入 review queue，人工确认后生成 eval case，并展示 CI gate。
5. Agent 尝试越权 file_reader 被拒绝并审计，再通过 rag_search 返回授权 citation。

这条演示链比 Graph RAG 或多 Agent 名词更能证明企业级能力。
