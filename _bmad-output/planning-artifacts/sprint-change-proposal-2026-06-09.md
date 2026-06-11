---
workflow: bmad-correct-course
project: 本地化多源知识增强 RAG + Agent 问答系统
date: 2026-06-09
mode: batch
status: applied-to-planning-documents
trigger_source: user-feedback-frontend-product-gap
scope_classification: moderate
updated_artifacts:
  - _bmad-output/planning-artifacts/epics.md
  - _bmad-output/implementation-artifacts/sprint-status.yaml
---

# Sprint Change Proposal: Epic 9 Frontend Direction Adjustment

## 1. Issue Summary

当前仓库后端已经具备企业级 RAG、citation、source resolve、diagnostics、audit、review queue 和 Tool Registry/Agent 基础，但用户主观感知仍然停留在“Open WebUI 聊天壳 + 若干外挂页面”。这带来三个直接问题：

1. 差异化后端能力没有在主交互层被看见，产品价值无法直观传达。
2. Open WebUI 作为主界面会持续限制 citation、tool events、diagnostics、governance 的一体化体验。
3. 继续围绕 Open WebUI 做轻量魔改，会把项目推向“兼容层越来越复杂，但主产品体验仍不属于自己”的状态。

因此需要在不新增 Epic 的前提下，调整 Epic 9 后半段方向：9.1-9.3 继续完成 Open WebUI 兼容收口；9.4 前移为真实 LLM provider 接入；9.5 再进入自有企业级前端主工作台建设。

## 2. Impact Analysis

### Epic Impact

- Epic 9 标题和说明从“Open WebUI 企业级集成增强与轻量魔改路线”调整为“Open WebUI 兼容收口与企业级前端工作台”。
- Story 9.1、9.2、9.3 保持不变，继续完成 Open WebUI 兼容层关键闭环。
- Story 9.4 从 Open WebUI 轻量定制包改为“真实 LLM Provider 接入与端到端聊天闭环”。
- Story 9.5 从 demo navigation 改为“企业级前端主工作台骨架与技术栈定版”。

### Story Impact

- 旧 9.4 的 Open WebUI 升级策略被降级为后续兼容性维护事项，不再作为当前主线 story。
- 新 9.4 成为 MVP 基础闭环 gate：没有真实 LLM provider，不进入主前端工作台。
- 旧 9.5 的“演示导航”需求不再独立存在，后续应嵌入主工作台叙事路径。
- sprint status 中 9.4、9.5 backlog 名称同步更新，避免后续 create-story 仍创建旧方向 story。

### Technical Impact

- 真实模型调用优先通过国际主流、生态成熟的 OpenAI-compatible provider 路线落地，后续再扩展其他 providers。
- 主前端将转向国际主流企业应用栈，默认优先 React + Next.js + TypeScript。
- Open WebUI 保留为 OpenAI-compatible 兼容入口和对外生态证明，不再是唯一主前端。
- 现有 `apps/web/sidecar` 与 `apps/web/governance` 的安全 allowlist、stale clearing、source resolve、diagnostics 和 review 流程将成为新前端的交互与安全参考，而不是最终主界面本身。

## 3. Recommended Approach

推荐路径：Direct Adjustment within Epic 9。

理由：

- 不新增 Epic，避免 backlog 和 sprint 流程分叉。
- 保留已完成的 Open WebUI 集成投资，不推翻 9.1-9.3。
- 先补真实模型闭环，再做主前端，避免做出“更好看的 fake 系统”。

实现顺序建议：

1. 完成 9.3 `Open WebUI function/tool bridge`。
2. 创建并实现 9.4 story，完成真实 LLM provider 接入与端到端聊天闭环。
3. 创建 9.5 story，定版前端技术栈、信息架构和企业级 UI 方向。
4. 后续再把 citation、source evidence、tool events、diagnostics、governance 入口做进主工作台。

## 4. Detailed Change Proposals

### Epics

Epic 9 title:

OLD:

```text
Open WebUI 企业级集成增强与轻量魔改路线
```

NEW:

```text
Open WebUI 兼容收口与企业级前端工作台
```

Rationale: 让 epic 名称准确反映“前半段兼容收口，后半段自有前端主工作台”的新路线。

### Story 9.4

OLD:

```text
可维护 Open WebUI 轻量定制包与升级策略
```

NEW:

```text
真实 LLM Provider 接入与端到端聊天闭环
```

Rationale: 当前最缺的是“系统能以真实模型跑起来”，这是 MVP 基础能力，不应后置。

关键新增约束：

- 至少一个真实 LLM provider 必须接通 `/chat`、`/chat/stream`、`/v1/chat/completions`。
- 实现继续经过 `packages/llm` Provider 抽象，不允许业务代码直接绑 SDK。
- 文档、配置、错误映射、audit、token usage 和 fake fallback 必须一起闭环。

### Story 9.5

OLD:

```text
企业安全能力演示导航与叙事入口
```

NEW:

```text
企业级前端主工作台骨架与技术栈定版
```

Rationale: 真实模型闭环之后，再进入主产品前端建设，顺序才符合 MVP 基础闭环。

关键新增约束：

- 自有前端成为主产品界面，Open WebUI 降级为兼容入口。
- 技术栈采用国际主流企业应用路线，默认优先 React + Next.js + TypeScript。
- UI 风格必须体现企业级工作台气质，而不是通用 AI 聊天壳。

## 5. Implementation Handoff

Scope classification: Moderate.

Routing:

- Developer / planning owner: 继续以当前 Epic 9 为主线，不新增 Epic。
- Next create-story target after 9.3: `9-4-真实-llm-provider-接入与端到端聊天闭环`
- Follow-up create-story: `9-5-企业级前端主工作台骨架与技术栈定版`

Success criteria:

1. Epic 9 后半段不再围绕 Open WebUI 长期魔改。
2. 真实 LLM provider 已被提升为 9.3 之后的 MVP 基础 gate。
3. 前端主界面方向已切换为自有企业级工作台。
4. 技术栈明确走国际主流 React/Next.js/TypeScript 路线。
5. sprint status 与 epics 保持一致，后续 create-story 不会再生成旧方向 9.4/9.5。
