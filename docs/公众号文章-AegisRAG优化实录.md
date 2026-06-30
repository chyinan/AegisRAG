# AegisRAG：一个 15K 行 Python 企业级 RAG 系统的「整容级」优化实录

> 2 天，12 个 AI Agent，7 篇 ADR，3,500 行新增代码，1,266 个测试——以及一个永不宕机的 Push→CI→Fix 循环。

---

## 项目是什么

**AegisRAG** 是一个**企业级私有知识 RAG + Agent 问答系统**。

不是 demo，不是玩具。是一个真正面向生产的项目：

- **19,078 行 Python**，6 个微服务，**1,266 个测试**
- FastAPI + PostgreSQL/pgvector + Redis + MinIO + Next.js
- Provider 抽象层：LLM / Embedding / VectorStore 均可热插拔
- 混合检索管线：Dense（向量）+ Sparse（BM25）+ RRF 融合 + LLM Reranker + **Graph RAG 知识图谱**
- 多租户权限：RBAC + ACL + tenant_id 在检索阶段强制过滤（LLM 永远不需要判断权限）
- 受控 Agent 运行时：Tool Registry + schema 校验 + timeout + rate limit + audit log
- 完整可观测：Prometheus + Grafana（8 面板）+ Jaeger 分布式追踪 + OpenTelemetry + W3C TraceContext
- Kubernetes 部署：完整 Helm Chart，含 PostgreSQL / Redis / MinIO / Prometheus / Grafana / Jaeger
- RAGAS 评估 + CI 质量门 + Codecov

**一句话**：一个从检索权限、到答案可追溯、到 Agent 可审计、到部署可观测，全链路覆盖的企业 RAG 平台。

---

## 团队工作流：Hermes Agent × 12 人 Kanban 流水线

这不是一个人写的。这是一个 **Hermes Agent 驱动的 12 人 AI 开发团队** 在干活：

```
Tech Lead（指挥）
  ├── Architect（架构设计）
  ├── Backend Dev（后端实现）
  ├── Frontend Dev（前端实现）
  ├── Reviewer #1（代码审查）
  ├── Reviewer #2（安全审查）
  ├── Reviewer #3（架构审查）
  ├── Aggregator（聚合打分）
  ├── Fix Agent（修复 < 90 分的项）
  └── QA（质量验证）
```

**工作模式**：

1. Tech Lead 发布任务，Architect 输出技术方案
2. Backend + Frontend **并行开发**
3. 三路审查**同时进行**（代码/安全/架构）
4. Aggregator 聚合评分，Score < 90 → **Fix Agent 自动修复**
5. 修复后重新进入审查循环，直到全绿

全程 **Vibe Coding**——Tech Lead 说「优化项目面向 15K 面试」，12 个 Agent 就开始干活。不写 PRD，不画流程图，纯自然语言驱动。

---

## 2 天干了什么

### Day 1：铲屎官模式——清积弊、修测试、去品牌化

| 动作 | 成果 |
|------|------|
| Graph RAG 端到端测试 | 162 节点 / 124 边 / 39.4s 构建，验证知识图谱可用 |
| DeepSeek 模型名废弃应对 | 全局扫描 7 个评估脚本，`deepseek-chat` → `deepseek-v4-flash` |
| Open WebUI 去品牌化 | 22 个文件重命名 + 删除 OWUI 容器 + 保留 Service Token 认证 |
| 133 个 lint 错误修复 | ruff 归零 |
| 5 个 flaky test 根治 | structlog/logging 状态污染 → conftest 全局 reset |

### Day 2：整容模式——肉眼可见的提升

| 维度 | 优化前 | 优化后 |
|------|:------:|:------:|
| README 测试数 | "130+" | **1,266** |
| 架构文档（ADR） | 4 篇 | **8 篇**（+Provider 抽象 / 混合检索 / Graph RAG / API 版本化） |
| K8s / Helm | 存在但未提及 | **技术栈表 + 部署章节** |
| 分布式追踪 | OTEL 代码存在但无人知晓 | **Mermaid 架构图 + 展示文档** |
| CI 流水线 | lint + test | **+ Docker 构建推送 ghcr.io** |
| 负载测试 | 5 并发 20s | **50 并发 60s**（发现并修复限流瓶颈） |
| 面试准备 | 0 | **3 份结构化叙事（3/10/30 分钟）** |
| 可观测展示 | Grafana 仪表板隐形 | **152 行展示文档 + 截图指南** |
| 检索实验 | 无 | **A/B 框架**（dense-only vs hybrid vs full pipeline + Wilcoxon 检验） |
| 部署策略 | 无 | **Canary 部署文档**（Nginx Ingress 灰度 + 租户级分流 + 质量门） |
| API 版本化 | 无 | **/v1/ 前缀 + 向后兼容 + 弃用 header** |
| 性能调优 | 无 | **cProfile 实测 case study**（发现 `redact_sensitive_data` 正则重编译占 35-55%） |

**总计：12 个新文件，~3,500 行新增，9 个 commits。**

---

## 核心工作流：Push → CI → Fix → Push 直到成功

这是这次优化中最值得讲的东西。

### 问题

Vibe Coding 的本质是「AI 写代码，人做决策」。但 AI 会写出 lint 错误、测试失败、边界条件遗漏。传统做法是：push → CI 红了 → 人工排查 → 人工修复 → 再 push → 可能又红。

**这个循环的人工部分，全被我们自动化了。**

### 方案

我们写了一个 `scripts/ci_verify.py`——一个 CI 自检脚本，每次 push 后自动运行：

```bash
python scripts/ci_verify.py --sha <commit> --wait 120
```

它会：
1. 通过 GitHub Checks API 拉取最新 commit 的所有 check run
2. 每 10 秒轮询一次，直到所有 job 完成或超时
3. 退出码：`0` = 全绿 ✅ / `1` = 有失败 ❌ / `2` = 还在跑 ⏳

然后我们在 Hermes Agent 的 skill 系统里注册了一个 `ci-auto-fix` 技能：

```
PUSH → 等 CI → 失败? → gh run view 读日志 → 定位根因 → 修 →
→ ruff check + pytest 本地验证 → commit → PUSH → 循环...
```

**直到 CI 全绿，Agent 不停止。**

### 实战效果

在写这篇文章的过程中，这个循环已经跑了 **5 轮**：

| 轮次 | 失败原因 | 修复耗时 |
|:----:|------|:---:|
| 1 | `ci_verify.py` 的 F401 + E501 lint 错误 | 60s |
| 2 | 3 个测试因 API 版本化 + Docker build 引入而挂掉 | 90s |
| 3 | README Build Status 删除后 2 个测试未同步 | 45s |
| 4 | 删除 Kanban 中间产物后 test 挂了 | 45s |
| 5 | ~~全绿~~ | — |

**人类做的事情**：说一句话「能不能在推送后自动检查是否通过 CI，没通过就继续修到通过为止」。

**Agent 做的事情**：设计脚本、写代码、处理边界条件（timeout、网络错误、`--sha HEAD` 解析失败）、创建 skill 固化流程、然后反复执行直到绿为止。

---

## 为什么这套工作流对 15K+ 面试有用

面试官看一个项目，通常 30 秒做决策。这 30 秒看的不是代码，是信号：

| 信号 | AegisRAG 有什么 |
|------|:--|
| **代码量 ≠ demo** | 15K+ 行 Python，6 个微服务 |
| **测试不只是有** | 1,266 个测试，CI 质量门，Codecov |
| **架构有思考** | 8 篇 ADR，每个大决策都有文档 |
| **不只是能跑** | Helm K8s 部署、Canary 策略、50 并发负载测试 |
| **不只是接 API** | OpenTelemetry + Jaeger 全链路追踪、Grafana 8 面板 |
| **不只是写代码** | A/B 检索实验 + Wilcoxon 显著性检验、性能 profiling case study |
| **能讲清楚** | 3/10/30 分钟结构化面试叙事 |

**最重要的是**：面试官问「你在这个项目里做了什么」，你不会说「我调了 OpenAI API 做了个问答系统」——你会说「我设计了一套混合检索管线，通过 Dense + BM25 + RRF + LLM Reranker 四阶段融合，把 Faithfulness 从 0.80 提升到 1.00，并写成了 ADR 文档」。

---

## 技术栈一览

```
后端：      FastAPI + Pydantic v2 + SQLAlchemy async + Alembic
数据库：    PostgreSQL 17 + pgvector (HNSW)
向量库：    pgvector / Milvus（可插拔）
缓存队列：  Redis + RQ
对象存储：  MinIO (S3-compatible)
前端：      Next.js (TypeScript) + Tailwind CSS
LLM：      OpenAI-compatible（DeepSeek / Qwen / Ollama）— Provider 抽象
Embedding： nomic-embed-text (768d) / OpenAI-compatible
Reranker：  LLM Reranker / OpenAI-compatible cross-encoder
检索：      Dense + Sparse (BM25) + Graph RAG → RRF fusion
可观测：    Prometheus + Grafana (8面板) + Jaeger + OpenTelemetry
评估：      RAGAS 0.3.9
部署：      Docker Compose + Kubernetes (Helm Chart)
CI/CD：     GitHub Actions + ruff + pytest + Codecov + ghcr.io
```

---

## 下一步

项目还在持续迭代。接下来的方向：

- **Reranker 真枪实弹**：用 BGE-Reranker 替代 LLM Reranker，降低延迟和成本
- **评估数据集扩充**：从 12 条 smoke query 扩展到 200+ 条多领域评测集
- **多模态支持**：图片 + PDF 扫描件 OCR → RAG

---

*项目地址：[github.com/chyinan/AegisRAG](https://github.com/chyinan/AegisRAG)*

*全流程由 Hermes Agent + DeepSeek V4 Pro 驱动，12 个 AI Agent 并行协作完成。*

*人类贡献：决策、审查、按回车。*
