---
name: 企业可信知识问答
status: draft
sources:
  - "{project-root}/AGENTS.md"
  - "{project-root}/PRD.md"
  - "{project-root}/docs/TECHNICAL_PREFERENCES.md"
  - "{project-root}/docs/EMPLOYMENT_PRODUCT_MARKET_OPTIMIZATION.md"
  - "https://docs.openwebui.com/features/workspace/knowledge/"
  - "https://docs.openwebui.com/features/plugin/functions/"
  - "https://docs.openwebui.com/features/plugin/tools/"
  - "https://docs.openwebui.com/features/authentication-access/rbac/permissions/"
updated: 2026-05-26
---

# 企业可信知识问答 — Experience Spine

## Foundation

单一响应式 Web 产品，第一阶段建立在 Open WebUI 的聊天、模型接入、知识空间、函数/工具和权限能力之上。`DESIGN.md` 是视觉身份参考；本文件定义业务行为、状态、交互和规则。

这里的“业务精细化微调”不是模型 fine-tuning，也不是把企业规则写进 prompt。标准实现是：Open WebUI 承担会话入口和基础操作外壳；本项目后端承担 AuthContext、ingestion、hybrid retrieval、citation、SSE、audit、eval、Tool Registry 和 Agent runtime。前端永远只展示后端确认过的状态，不判断权限、不补造引用、不推断检索结果。

## Information Architecture

| Surface | Reached from | Audience | Purpose |
| --- | --- | --- | --- |
| Knowledge Chat | Open WebUI chat home | 员工、业务用户 | 提问、查看流式回答、打开 citation、继续追问 |
| Source Inspector | Citation chip / answer metadata | 员工、管理员 | 查看授权片段、文档版本、页码、chunk、source metadata |
| Knowledge Admin | Admin nav / workspace entry | 知识管理员 | 上传文档、设置 ACL、查看 ingestion 和 embedding job 状态 |
| Retrieval Diagnostics | Admin nav / request_id link | AI 工程师、管理员 | 复盘 dense、sparse、RRF、rerank、context packing 和 latency |
| Eval Reports | Admin nav | AI 工程师、项目展示 | 查看 hit rate、citation coverage、no-answer correctness、ACL 隔离 |
| Agent Run Review | Phase 4 / `/agent/run` | 授权业务用户、管理员 | 查看 tool_call、tool_result、max_steps、final answer 和 audit |
| Settings & Permissions | Admin nav | 系统管理员 | 映射 Open WebUI 用户/组到后端 tenant、roles、department、permissions |

MVP 可以只实现 Knowledge Chat、Source Inspector、Knowledge Admin 的最小集合。Retrieval Diagnostics 和 Eval Reports 至少要有后端数据与一个可访问入口；不需要抢先做完整仪表盘。

## Standard Design Implementation

| Layer | Standard | Rule |
| --- | --- | --- |
| Open WebUI shell | 保留聊天、会话和基础知识入口 | 不 fork-first；优先通过配置、扩展点或 sidecar 页面接入 |
| Backend chat adapter | OpenAI-compatible adapter backed by `/chat` 对外表现为模型端点 | 所有请求必须注入 `request_id`、`tenant_id`、`user_id`、`session_id` |
| Citation renderer | 优先结构化 citation chip；不可行时降级为稳定 Markdown citation block | citation 只能来自后端 `citations[]`，不能由 LLM 或前端猜测 |
| Source detail | `POST /sources/resolve` | 点击 citation 前再次校验 AuthContext、tenant、RBAC、ACL、soft delete 和 version visibility；无权限时不泄露文档存在性 |
| Upload and jobs | 最小管理面或 Open WebUI 外链页面 | `/upload` 返回 job id；页面展示 parse、chunk、embedding、index 状态 |
| Diagnostics | request_id 驱动的只读页面 | 默认管理员可见；日志只显示摘要和 metadata，不显示企业全文 |
| Agent tools | Open WebUI tool/function 入口可作为触发层 | 真正工具执行必须经过本项目 Tool Registry、permission、timeout、rate limit、audit |

## Business Fine-Tuning Rules

1. **可信信号优先于聊天装饰。** 每次回答至少暴露当前知识范围、request_id、citation 状态和无答案策略结果。
2. **Open WebUI 是入口，不是治理边界。** 用户、组和权限可以在 Open WebUI 中呈现，但 retrieval ACL、tenant filter、tool permission 必须由后端执行。
3. **citation 是一级交互。** 引用不能只作为回答末尾的脚注；它应可点击、可复核、可追踪到 document/version/page/chunk。
4. **无答案要设计成成功状态。** 当上下文不足时，UI 应显示“无法从授权资料确认”，并给出可操作下一步，例如调整范围、上传资料、联系管理员。
5. **检索诊断只对有权限的人开放。** 普通员工看到来源和范围；管理员看到阶段分数、延迟、错误码和 eval 关联。
6. **Agent 事件必须可审计。** 工具调用在 UI 中显示为受控事件，不显示成模型自然语言的一部分。
7. **任何状态都可复制 request_id。** 失败、拒答、越权、job retry、tool error 都必须给排查入口。

## Voice and Tone

微文案表达确定性边界。品牌语气和视觉姿态见 `DESIGN.md`。

| Do | Don't |
| --- | --- |
| “无法从当前授权资料确认。” | “我不太确定，但也许是……” |
| “引用 3 个来源，均来自 HR 制度库。” | “答案来源可靠。” |
| “索引仍在处理中，完成后可检索。” | “上传成功，可以问了。” |
| “你当前范围：华东区 / HR 制度库。” | “系统已为你筛选相关资料。” |
| “工具调用已停止：达到 max_tool_calls。” | “Agent 觉得已经够了。” |

## Component Patterns

| Component | Use | Behavioral rules |
| --- | --- | --- |
| Scope badge | Chat header, answer metadata | 显示当前 tenant、department、knowledge scope。缺少 AuthContext 时禁用提问并提示重新登录或选择范围。 |
| Query composer | Knowledge Chat | 输入框不承担权限选择。权限来自登录上下文；可选知识范围选择只能收窄范围，不能扩大权限。 |
| Streaming answer | Knowledge Chat | SSE `token` 到来时逐步渲染；`citation` 事件到来前可以显示“来源确认中”；`final` 后锁定 citation 列表。 |
| Citation chip | Answer footer and inline references | 点击打开 Source Inspector。chip 必须包含 title 或 source、version、page 或 chunk 标识。 |
| Source Inspector | Right drawer / mobile sheet | 打开时重新请求授权片段。显示文档 metadata、版本、页码、chunk id、title_path、retrieval_method 和 score 摘要。 |
| No-answer panel | Answer area | 作为正式回答状态展示，不算错误。提供“查看检索范围”“上传或请求资料”“复制 request_id”。 |
| Job status row | Knowledge Admin | 状态顺序：uploaded -> parsing -> parsed -> chunking -> chunked -> embedding -> indexing -> retrieval_ready；失败状态区分 failed_retryable 和 failed_terminal，并显示安全错误摘要。 |
| Retrieval trace | Diagnostics | 以 request_id 为主键。展示 dense top_k、sparse top_k、RRF、rerank、threshold、context packing、latency 和 error_code。 |
| Tool event row | Agent Run Review | `tool_call` 显示工具名、权限、参数摘要；`tool_result` 显示结果摘要、latency、status。默认不展开原始输出。 |

## State Patterns

| State | Surface | Treatment |
| --- | --- | --- |
| Cold load | Knowledge Chat | 先显示会话骨架、当前范围骨架；AuthContext 未解析前禁用发送。 |
| Auth missing | Global | 结构化错误：`AUTH_CONTEXT_REQUIRED`。不显示任何知识库或文档名称。 |
| Empty authorized scope | Knowledge Chat | “当前范围没有可检索资料。” 提供管理员联系或上传入口，视权限而定。 |
| Streaming with pending citation | Answer | 正文可流式出现，来源区域显示 pending；`final` 事件未到时不允许复制“带来源答案”。 |
| Citation unavailable | Answer | 显示无答案或低置信来源不足状态；禁止生成假 citation。 |
| Index pending | Knowledge Admin / Chat | 对上传者显示 job 状态；对普通查询用户只说明“部分资料仍在索引中”，不暴露未授权文档。 |
| Job failed retryable | Knowledge Admin | 显示阶段、错误码、retry action、last_attempt_at。 |
| Permission denied | Any source/tool action | 返回通用权限信息和 request_id，不说明未授权资源是否存在。 |
| Agent max_steps reached | Agent Run Review | 停止工具调用，显示 final status、已执行 steps、最后一次安全观察。 |
| Eval regression | Eval Reports | 标记指标变化、关联样例和最近配置变更；不在员工端暴露。 |

## Interaction Primitives

- 点击 citation 打开 Source Inspector；在桌面端为右侧抽屉，在移动端为底部 sheet。
- `Copy answer with citations` 只在 `final` 事件到达后启用。
- `Copy request_id` 出现在回答 metadata、错误状态、job 状态、diagnostics 和 agent run。
- 员工端筛选只允许收窄范围，例如选择“HR 制度库”；不能通过 UI 选择超出权限的 tenant 或 ACL。
- 管理端表格必须支持按 status、source_type、created_by、updated_at、error_code 筛选。
- 禁止 hover-only 关键操作；移动和键盘用户必须能打开 citation、复制 request_id、重试 job。
- 禁止把 retrieval trace、tool raw output、完整企业文档正文默认渲染到聊天流中。

## Accessibility Floor

- WCAG 2.2 AA。视觉对比由 `DESIGN.md` 颜色规则保障。
- Streaming answer 使用 `aria-live="polite"`；错误和权限拒绝使用可被屏幕阅读器发现的 alert 区域。
- Citation chip、source drawer、job row、tool event 都必须可键盘聚焦，并有清晰名称。
- 焦点环使用 `{colors.focus-ring}`；任何 drawer/sheet 打开时焦点进入标题，关闭后返回触发 chip。
- 表格中的 id、score、latency 不只靠颜色表达状态。
- 页面最长中文字段、文件名、URL、document_id 必须换行或截断并提供完整值读取方式。

## Responsive & Platform

| Breakpoint | Behavior |
| --- | --- |
| `>= 1200px` | 左导航 + 中间 chat + 右 Source Inspector / Diagnostics。 |
| `768-1199px` | 左导航可折叠；Source Inspector 覆盖右侧但不遮挡 composer。 |
| `< 768px` | 单列 chat；citation 和 diagnostics 使用 bottom sheet；管理表格改为筛选列表 + 详情页。 |

移动端支持查询、查看 citation、查看 job 摘要和复制 request_id。高密度诊断、批量上传、eval 分析优先桌面。

## Inspiration & Anti-patterns

- **Lifted from Open WebUI:** chat-first 心智、模型/会话入口、知识空间作为低成本前端承载。
- **Lifted from observability tools:** request_id 驱动排查、阶段化 latency、错误码和只读诊断。
- **Lifted from enterprise document systems:** 版本、ACL、状态、来源页码是文档资产的一部分，不是回答装饰。
- **Rejected — prompt-only governance:** 权限、citation、工具策略不能放在系统提示里。
- **Rejected — complete custom frontend in MVP:** 自研复杂前端会挤占 ingestion、retrieval、citation、RBAC、eval 的实现优先级。
- **Rejected — confidence theater:** 不显示未经校准的“可信度 92%”。可展示的是来源数量、rerank 分数、无答案策略和 eval 指标。
- **Rejected — agent as magic automation:** 没有 Tool Registry、max_steps、timeout 和 audit 的 agent UI 不进入主线。

## Key Flows

### Flow 1 — 林敏查询 HR 制度条款

1. 林敏打开 Open WebUI 中的企业知识问答会话。
2. Chat header 显示 scope badge：`浅川集团 / HR 制度库 / 华东区`。
3. 她输入“试用期员工可以申请年假吗？”。
4. 系统流式输出回答；来源区域先显示“来源确认中”。
5. `final` 事件到达后，回答底部出现 3 个 citation chips：制度名、version、page。
6. **Climax:** 林敏点击第一个 citation，Source Inspector 打开授权片段，显示 document_id、version_id、chunk_id、page 和 title_path。她确认答案来自制度原文，而不是模型猜测。
7. 她复制“带 citation 的答案”发给员工。

Failure: 检索结果不足 -> 回答区显示“无法从当前授权资料确认”，并提供 request_id 和“联系知识管理员补充资料”。

### Flow 2 — 赵强上传产品手册并观察索引

1. 赵强进入 Knowledge Admin，点击上传。
2. 他选择 PDF 和 Markdown，设置 source_type、ACL 和版本说明。
3. 上传接口立即返回 document_id、version_id、job_id。
4. Job status row 依次显示 parsing、chunking、embedding、indexing。
5. **Climax:** 状态变为 retrieval_ready，详情显示 chunk_count、checksum、embedding_model、embedding_dim 和 sparse index 状态。
6. 赵强发起一次测试查询，答案返回该文档的 citation。

Failure: embedding provider 超时 -> job 显示 retryable error、error_code、last_attempt_at 和 retry 按钮；上传本身不被标记为失败。

### Flow 3 — 陈宇复盘一次引用错误

1. 业务反馈“答案引用不准确”，陈宇拿到 request_id。
2. 他打开 Retrieval Diagnostics，粘贴 request_id。
3. 页面显示 dense top_k、sparse top_k、RRF 合并、rerank 分数、context packing 结果和 final citations。
4. 他发现正确 chunk 被 sparse 召回但 rerank 后被阈值过滤。
5. **Climax:** 陈宇把该问题加入 eval dataset，并记录失败分类为 `rerank_threshold_false_negative`。
6. 调整阈值后，Eval Reports 中 citation coverage 和 hit rate 回归通过。

Failure: request_id 不存在或无权限 -> 页面只显示结构化错误，不暴露 query 原文。

### Flow 4 — 王珂运行受控 Agent 计算合同折扣

1. 王珂在 Agent Run Review 入口发起“查合同条款并计算折扣金额”。
2. UI 显示本次 agent 的 max_steps、max_tool_calls、可用工具：`rag_search`、`calculator`。
3. Agent 调用 `rag_search`，Tool event row 展示权限校验通过和参数摘要。
4. Agent 调用 `calculator`，Tool event row 展示计算表达式摘要和结果。
5. **Climax:** 最终回答展示折扣金额，并绑定合同条款 citation；管理员可打开 audit 看到每次 tool_call。
6. 如果达到 max_tool_calls，Agent 停止并返回结构化状态，而不是继续循环。

Phase gate: 该流程只能在 Tool Registry、permission、timeout、rate_limit、audit、max_steps 和 max_tool_calls 全部实现后上线。

## Resolved Decisions and Open Questions

### Resolved Decisions

1. Citation MVP 使用 `POST /sources/resolve` 返回授权片段和 source metadata；原文页码跳转后续由文档预览能力增强。
2. Open WebUI 首个集成路径固定为 OpenAI-compatible chat adapter backed by `/chat`；sidecar 只承载 Source Inspector、上传/job、诊断、eval 等治理入口。
3. MVP 认证方案固定为开发/测试模拟 AuthContext + 轻量 JWT adapter。员工端、管理员端和 Open WebUI adapter 看到的 scope、permission denied、request_id 和 source visibility 都必须来自后端解析后的同一 `AuthContext` DTO。

### Remaining Open Questions

1. 是否增加最小自定义管理面承载 upload/job/log/eval，还是全部通过 Open WebUI 入口和后端 API 验证？
