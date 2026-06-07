---
name: 企业可信知识问答
description: Open WebUI 基础上的企业 RAG 业务微调视觉规范，强调授权范围、来源引用、索引状态和可审计操作。
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
colors:
  surface-base: '#F7F8FA'
  surface-panel: '#FFFFFF'
  surface-muted: '#EEF2F6'
  surface-code: '#EAF0F7'
  ink-primary: '#1D2433'
  ink-secondary: '#526071'
  ink-muted: '#7A8493'
  border-subtle: '#D7DDE5'
  border-strong: '#A8B2C1'
  primary: '#255C99'
  primary-foreground: '#FFFFFF'
  source: '#26735E'
  source-foreground: '#FFFFFF'
  index: '#A86500'
  index-foreground: '#241600'
  warning: '#C98200'
  danger: '#B42318'
  danger-foreground: '#FFFFFF'
  focus-ring: '#6AA3FF'
typography:
  page-title:
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif'
    fontSize: 24px
    fontWeight: '650'
    lineHeight: '1.25'
    letterSpacing: '0'
  section-title:
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif'
    fontSize: 16px
    fontWeight: '650'
    lineHeight: '1.35'
    letterSpacing: '0'
  body:
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif'
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.6'
    letterSpacing: '0'
  label:
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif'
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1.35'
    letterSpacing: '0'
  mono:
    fontFamily: 'JetBrains Mono, ui-monospace, SFMono-Regular, monospace'
    fontSize: 12px
    fontWeight: '400'
    lineHeight: '1.45'
    letterSpacing: '0'
rounded:
  sm: 4px
  md: 6px
  lg: 8px
  full: 9999px
spacing:
  '1': 4px
  '2': 8px
  '3': 12px
  '4': 16px
  '5': 20px
  '6': 24px
  '8': 32px
  '10': 40px
  gutter: 24px
  sidebar: 280px
  inspector: 360px
components:
  scope-badge:
    background: '{colors.surface-muted}'
    foreground: '{colors.ink-secondary}'
    border: '1px solid {colors.border-subtle}'
    radius: '{rounded.full}'
  citation-chip:
    background: '{colors.surface-panel}'
    foreground: '{colors.source}'
    border: '1px solid {colors.source}'
    radius: '{rounded.full}'
  answer-message:
    background: '{colors.surface-panel}'
    foreground: '{colors.ink-primary}'
    border: '1px solid {colors.border-subtle}'
    radius: '{rounded.lg}'
  source-inspector:
    background: '{colors.surface-panel}'
    foreground: '{colors.ink-primary}'
    border: '1px solid {colors.border-subtle}'
    radius: '{rounded.lg}'
  job-status-row:
    background: '{colors.surface-panel}'
    foreground: '{colors.ink-primary}'
    border: '1px solid {colors.border-subtle}'
    radius: '{rounded.md}'
  audit-event:
    background: '{colors.surface-muted}'
    foreground: '{colors.ink-secondary}'
    border: '1px solid {colors.border-subtle}'
    radius: '{rounded.md}'
  danger-banner:
    background: '#FFF1F0'
    foreground: '{colors.danger}'
    border: '1px solid #F4B8B2'
    radius: '{rounded.md}'
---

## Brand & Style

企业可信知识问答不是一个新的聊天品牌，也不是 Open WebUI 的视觉替代品。它是在 Open WebUI 现有交互外壳上增加一层企业可信信号：当前授权范围、可追溯引用、索引状态、检索诊断和受控工具调用。

视觉姿态应保持安静、密集、可复盘。界面看起来像生产运维和知识治理工具，而不是营销页、AI 聊天玩具或模型能力秀。没有大面积渐变、装饰插画、情绪化动效或夸张空状态。每个新增视觉元素都必须解释一个生产问题：这条回答从哪来、用户是否有权看、索引是否完成、失败在哪个阶段、工具是否被授权。

## Colors

- **Surface Base (`#F7F8FA`)** 是应用背景，降低长时间阅读疲劳。
- **Surface Panel (`#FFFFFF`)** 承载回答、引用、状态行和抽屉等可操作内容。
- **Primary Blue (`#255C99`)** 只用于主操作、当前路由和焦点明确的系统动作，不用作装饰。
- **Source Green (`#26735E`)** 是 citation 和已验证来源的专用颜色。绿色不代表“答案一定正确”，只代表“这段内容有可打开的来源绑定”。
- **Index Amber (`#A86500`)** 标识 ingestion、embedding、indexing、retry 等处理中状态。
- **Danger Red (`#B42318`)** 只用于权限拒绝、不可恢复失败、越权风险和删除确认。

避免把语义颜色用于美化。一个答案块同时出现超过两种强调色，通常说明信息层级没有设计清楚。

## Typography

继承 Open WebUI 的系统字体方向，新增业务层只使用小而清晰的工具型排版。`page-title` 用于管理和诊断页面标题；聊天消息、引用摘要、job 状态和日志均使用 `{typography.body}`；chunk id、request id、trace id、document id、version id 使用 `{typography.mono}`。

禁止使用 hero 级字号包装普通管理面板。表格和状态列表中的最长字段必须换行或截断并提供 tooltip，不允许撑破容器。

## Layout & Spacing

布局继承 Open WebUI 的 chat-first 结构。桌面端推荐三层密度：

| 区域 | 宽度 | 用途 |
| --- | --- | --- |
| 左侧导航 | `{spacing.sidebar}` | 会话、知识空间、管理入口 |
| 中间主区 | 自适应 | 提问、回答、流式输出、主要任务 |
| 右侧检查器 | `{spacing.inspector}` | citation、来源预览、retrieval trace、tool events |

移动端不强行压缩三栏。右侧检查器变为底部 sheet；管理和诊断表格优先提供筛选后的摘要，再进入详情。

间距使用 4px 基础刻度。工具面板和列表使用紧凑节奏，文档预览和答案正文允许更多行高。页面 section 不做浮动卡片；卡片只用于重复项、消息、状态行和弹层。

## Elevation & Depth

深度主要靠色块、边框和信息层级表达。阴影只用于弹层、抽屉和悬浮菜单；不通过重阴影制造层次。诊断面板、日志行和来源预览应像可审计记录，而不是营销卡片。

## Shapes

圆角上限为 `{rounded.lg}`。按钮、输入框、状态行使用 `{rounded.md}`；citation chip 和 scope badge 可使用 `{rounded.full}`，因为它们是紧凑元数据，不是主要内容容器。不要使用超大圆角卡片包装整页。

## Components

| Component | Visual rule |
| --- | --- |
| Scope badge | 显示当前 tenant、department 或知识范围。使用 `{components.scope-badge}`，不得用醒目颜色伪装成权限证明。 |
| Answer message | 使用 `{components.answer-message}`。回答正文保持可读，metadata 收敛在底部，不抢正文。 |
| Citation chip | 使用 `{components.citation-chip}`。文本格式优先为 `文档名 · v{version} · p{page}`；缺页码时显示 `source metadata`，不得伪造页码。 |
| Source inspector | 使用 `{components.source-inspector}`。标题区显示 document、version、chunk、ACL 可见性摘要；正文区只展示授权片段。 |
| Job status row | 使用 `{components.job-status-row}`。状态色只出现在左侧细条或图标，不整行染色。 |
| Retrieval trace row | 使用 `{typography.mono}` 展示 request_id、top_k、rerank score、latency；默认仅管理员可见。 |
| Tool event | 使用 `{components.audit-event}`。`tool_call` 与 `tool_result` 使用同一视觉体系，避免看起来像普通聊天内容。 |
| Danger banner | 使用 `{components.danger-banner}`。权限拒绝、跨租户风险、删除确认必须可感知但不泄露未授权资源存在性。 |

## Do's and Don'ts

| Do | Don't |
| --- | --- |
| 保留 Open WebUI 的基础聊天体验，只增加企业可信信号 | 为了“产品化”重做整套聊天 UI |
| citation、scope、job、trace、audit 都有稳定视觉位置 | 把来源和日志塞进自然语言回答里 |
| 员工端展示少量必要信息，管理员端展示诊断细节 | 让普通用户看到 dense/sparse/rerank 内部细节 |
| 使用后端返回的结构化 metadata 渲染来源 | 让前端或 LLM 猜测 citation |
| 权限、工具、检索状态用后端状态驱动 | 用 prompt 文案暗示权限和安全 |
| 状态颜色只表达语义 | 用绿色表达“AI 很可靠” |

