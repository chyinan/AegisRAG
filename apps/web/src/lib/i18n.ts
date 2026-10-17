// pattern: Functional Core

export type Language = "en" | "zh";

export type LocalizedText = {
  en: string;
  zh: string;
};

export function text(value: LocalizedText, language: Language): string {
  return value[language];
}

export const languageLabel: Record<Language, string> = {
  en: "English",
  zh: "中文"
};

export const uiText = {
  language: { en: "Language", zh: "语言" },
  trustedWorkbench: { en: "Trusted Enterprise Knowledge Workbench", zh: "可信企业知识工作台" },
  authIntro: {
    en: "Sign in to your tenant knowledge workspace. Permissions, document access, citations and tool authorization are verified by the backend.",
    zh: "登录你的租户知识工作台。权限、文档访问、引用和工具授权均由后端验证。"
  },
  securityBoundary: { en: "Security boundary", zh: "安全边界" },
  securityBoundaryCopy: {
    en: "No browser token storage and no frontend role or permission expansion.",
    zh: "不在浏览器保存 token，不在前端扩大 roles 或 permissions。"
  },
  localPersonas: { en: "Local personas", zh: "本地角色" },
  localPersonasHelp: {
    en: "For dev/test auth headers only. Production should use enterprise SSO/JWT.",
    zh: "仅用于 dev/test auth headers，生产环境应接入企业 SSO/JWT。"
  },
  enterpriseJwt: { en: "Enterprise JWT", zh: "企业 JWT" },
  bearerToken: { en: "Bearer token", zh: "Bearer token" },
  jwtPlaceholder: { en: "Paste JWT for backend AuthContext", zh: "粘贴用于后端 AuthContext 的 JWT" },
  continueWithJwt: { en: "Continue with JWT", zh: "使用 JWT 继续" },
  usernameLogin: { en: "Username / Password", zh: "用户名 / 密码登录" },
  tenantId: { en: "Tenant ID", zh: "租户 ID" },
  tenantIdPlaceholder: { en: "Enter tenant ID", zh: "输入租户 ID" },
  username: { en: "Username", zh: "用户名" },
  usernamePlaceholder: { en: "Enter username", zh: "输入用户名" },
  password: { en: "Password", zh: "密码" },
  passwordPlaceholder: { en: "Enter password", zh: "输入密码" },
  signIn: { en: "Sign In", zh: "登录" },
  signingIn: { en: "Signing in...", zh: "登录中..." },
  loginError: { en: "Login failed", zh: "登录失败" },
  currentIdentity: { en: "Current identity", zh: "当前身份" },
  signOut: { en: "Sign out", zh: "退出登录" },
  mainTitle: { en: "Enterprise Knowledge Operations", zh: "企业知识操作台" },
  mainSubtitle: {
    en: "Trusted RAG operations console; Open WebUI remains a compatible entry point.",
    zh: "面向可信 RAG 闭环的企业知识操作台；Open WebUI 保留为兼容入口。"
  },
  sidecar: { en: "Sidecar", zh: "辅助面板" },
  rbacMetric: { en: "RBAC enforced by backend", zh: "RBAC 由后端执行" },
  citationMetric: { en: "Citation requires source resolve", zh: "Citation 需二次授权" },
  streamingMetric: { en: "SSE streaming ready", zh: "SSE 流式就绪" },
  evidence: { en: "Evidence", zh: "证据" },
  diagnostics: { en: "Diagnostics", zh: "诊断" },
  uploadQueued: { en: "Upload queued", zh: "上传已入队" },
  askTitle: { en: "Ask with citations", zh: "带引用问答" },
  askHelp: {
    en: "Submit only the question and optional scope; the frontend never constructs tenant, roles, or provider prompts.",
    zh: "只提交问题和可选收窄范围；前端不构造 tenant、roles 或 provider prompt。"
  },
  askEmpty: {
    en: "Ask a question against the authorized knowledge base. Copy remains disabled until the final cited answer.",
    zh: "请输入企业知识问题。回答完成前，复制按钮会保持禁用。"
  },
  cannotConfirm: { en: "Cannot confirm from the authorized sources.", zh: "无法从当前授权资料确认。" },
  askPlaceholder: {
    en: "Ask about policies, contracts, standards, or engineering knowledge in your authorized scope...",
    zh: "询问当前授权知识库中的制度、合同、规范或研发知识..."
  },
  noAnswerNote: {
    en: "No-answer is a valid outcome; sources are never fabricated.",
    zh: "no-answer 是成功状态，不补造来源。"
  },
  conversationHistory: { en: "Conversation history", zh: "对话历史" },
  currentSession: { en: "Current session", zh: "当前会话" },
  restoredHistory: { en: "History restored from backend memory", zh: "已从后端记忆恢复历史" },
  newConversation: { en: "New conversation", zh: "新对话" },
  retryHistory: { en: "Retry history", zh: "重试恢复历史" },
  historyTemporarilyUnavailable: {
    en: "Chat history is temporarily unavailable. Retry restoring it or start a new conversation before asking.",
    zh: "对话历史暂时无法加载。请重试恢复历史或开始新对话后再提问。"
  },
  historyUnavailable: {
    en: "Unable to restore this chat history. A new session will be used.",
    zh: "无法恢复该会话历史，将使用新会话。"
  },
  quickImport: { en: "Quick import", zh: "快捷导入" },
  quickImportDescription: {
    en: "Quick import only includes common fields. Advanced metadata and version management live in Knowledge Base.",
    zh: "快捷导入只包含常用字段；高级 metadata 和版本管理在 Knowledge Base 页面处理。"
  },
  closeImport: { en: "Close import drawer", zh: "关闭导入抽屉" },
  governancePlaceholder: {
    en: "This surface keeps a stable entry and safe empty state. Detailed governance still opens `/governance`.",
    zh: "本页面固定稳定入口和安全空状态；未直接接入的治理明细继续跳转现有 /governance。"
  },
  openGovernance: { en: "Open governance", zh: "打开治理页面" },
  backendFactsOnly: { en: "Backend facts only", zh: "仅后端事实" },
  backendFactsCopy: {
    en: "No fake data, raw query, prompt, chunk content, SQL, vectors, provider payloads, or secrets.",
    zh: "不展示假数据、不展示 raw query、prompt、chunk content、SQL、vectors、provider payload 或 secrets。"
  },
  auditExplorerTitle: { en: "Audit Explorer", zh: "审计检索器" },
  auditExplorerHelp: {
    en: "Query backend-confirmed audit summaries by safe identifiers. Tenant and permission scope stay backend-owned.",
    zh: "按安全标识查询后端确认的审计摘要。tenant 和权限范围仍由后端决定。"
  },
  searchLogs: { en: "Search logs", zh: "查询日志" },
  prepareExport: { en: "Prepare export", zh: "准备导出" },
  copyExportJson: { en: "Copy export JSON", zh: "复制导出 JSON" },
  auditFilters: { en: "Audit filters", zh: "审计过滤条件" },
  auditResults: { en: "Audit results", zh: "审计结果" },
  auditAssociations: { en: "Associations", zh: "关联摘要" },
  noAuditRecords: {
    en: "No audit records found for this authorized filter.",
    zh: "当前授权过滤条件下没有审计记录。"
  },
  auditError: {
    en: "Audit Explorer cannot display records for this request. Only a safe failure summary is shown.",
    zh: "审计检索器无法展示本次请求记录；这里只显示安全失败摘要。"
  },
  auditExportReady: { en: "Audit export prepared", zh: "审计导出已准备" },
  auditSafeBoundary: {
    en: "Raw prompts, queries, chunk text, SQL, vectors, provider payloads, tool arguments, and secrets are never rendered.",
    zh: "不渲染 raw prompts、queries、chunk text、SQL、vectors、provider payload、tool arguments 或 secrets。"
  },
  createdAtFrom: { en: "Created from", zh: "创建时间起" },
  createdAtTo: { en: "Created to", zh: "创建时间止" },
  dateTimeFilterPlaceholder: { en: "YYYY-MM-DD HH:mm", zh: "YYYY-MM-DD HH:mm" },
  reviewQueueTitle: { en: "Review Queue", zh: "审阅队列" },
  reviewQueueHelp: {
    en: "Inspect backend-created review items and safe eval candidate previews without exposing raw evidence.",
    zh: "查看后端创建的审阅项和安全 eval 候选预览，不暴露原始证据。"
  },
  loadReviewItems: { en: "Load review items", zh: "加载审阅项" },
  noReviewItems: { en: "No review items found in the current authorized scope.", zh: "当前授权范围内没有审阅项。" },
  reviewError: {
    en: "Unable to load review items. Only a safe failure summary is shown.",
    zh: "无法加载审阅项；这里只显示安全失败摘要。"
  },
  evalEvidenceTitle: { en: "Eval Evidence", zh: "评估证据" },
  evalEvidenceHelp: {
    en: "Browse safe regression report summaries. Dataset content, prompts, and raw provider output stay hidden.",
    zh: "浏览安全回归报告摘要。数据集正文、prompt 和原始 provider 输出保持隐藏。"
  },
  loadEvalReports: { en: "Load reports", zh: "加载报告" },
  noEvalReports: { en: "No eval reports are visible in the current environment.", zh: "当前环境没有可见评估报告。" },
  evalError: {
    en: "Unable to load eval evidence. Raw report payloads are not shown.",
    zh: "无法加载评估证据；不会显示原始报告 payload。"
  },
  agentConsoleTitle: { en: "Agent Run Console", zh: "Agent 运行控制台" },
  agentConsoleHelp: {
    en: "Run the governed Agent through backend Tool Registry limits, permissions, timeout, and audit logging.",
    zh: "通过后端 Tool Registry 的权限、步数、超时和审计边界运行受控 Agent。"
  },
  agentInput: { en: "Agent input", zh: "Agent 输入" },
  agentInputPlaceholder: {
    en: "Ask the governed Agent to use approved tools within this identity scope...",
    zh: "要求受控 Agent 在当前身份范围内使用已批准工具..."
  },
  runAgent: { en: "Run agent", zh: "运行 Agent" },
  agentError: {
    en: "Agent run did not complete. Copy request_id for audit follow-up when available.",
    zh: "Agent 运行未完成；如有 request_id，可复制后审计跟进。"
  },
  identityBoundariesTitle: { en: "Identity Boundaries", zh: "身份边界" },
  identityBoundariesHelp: {
    en: "This page shows browser-held identity context only. Backend AuthContext remains authoritative.",
    zh: "本页只显示浏览器持有的身份上下文；后端 AuthContext 仍是权威来源。"
  },
  currentPermissions: { en: "Current permissions", zh: "当前权限" },
  currentRoles: { en: "Current roles", zh: "当前角色" },
  noPermissionExpansion: {
    en: "The frontend cannot expand tenant, roles, permissions, ACL, citation, source visibility, or tool authority.",
    zh: "前端不能扩大 tenant、roles、permissions、ACL、citation、source visibility 或工具权限。"
  },
  knowledgeTitle: { en: "Knowledge Base", zh: "知识库" },
  knowledgeHelp: {
    en: "Business-readable by default; engineering index details drill into Diagnostics or governance.",
    zh: "业务可读优先，工程索引状态可下钻到 Diagnostics 或 governance。"
  },
  documents: { en: "Documents", zh: "文档" },
  refresh: { en: "Refresh", zh: "刷新" },
  title: { en: "Title", zh: "标题" },
  sourceType: { en: "Source type", zh: "来源类型" },
  scopeAcl: { en: "Scope / ACL", zh: "范围 / ACL" },
  status: { en: "Status", zh: "状态" },
  updated: { en: "Updated", zh: "更新时间" },
  actions: { en: "Actions", zh: "操作" },
  deleteDocument: { en: "Delete document", zh: "删除文档" },
  deleteDocumentConfirm: {
    en: "Delete this document and its indexed chunks? This removes it from retrieval.",
    zh: "删除这个文档及其索引 chunks？删除后将不再参与检索。"
  },
  deleteDocumentError: {
    en: "Delete did not complete. Check document:manage permission and try again.",
    zh: "删除未完成。请检查 document:manage 权限后重试。"
  },
  documentListError: {
    en: "Unable to load the document review list. Unauthorized document names or excerpts are never shown.",
    zh: "无法读取文档审阅列表。不会展示任何未授权文档名或历史片段。"
  },
  noVisibleDocuments: {
    en: "No documents are visible in the current authorized scope.",
    zh: "当前授权范围没有可展示文档。"
  },
  noVisibleDocumentsHelp: {
    en: "Users with upload permission can import documents; others should contact a knowledge manager.",
    zh: "有上传权限的用户可以先导入资料；其他用户可联系知识管理员。"
  },
  importDocument: { en: "Import document", zh: "导入文档" },
  asyncIngestion: { en: "async ingestion", zh: "异步入库" },
  file: { en: "File", zh: "文件" },
  chooseFile: { en: "Choose file", zh: "选择文件" },
  noFileSelected: { en: "No file selected", zh: "未选择文件" },
  selectedFile: { en: "Selected", zh: "已选中" },
  sourceReference: { en: "Source reference", zh: "来源引用" },
  aclPreset: { en: "ACL preset", zh: "ACL 预设" },
  uploadAndCreateJob: { en: "Upload and create job", zh: "上传并创建任务" },
  uploadError: {
    en: "Upload did not complete. Check permissions, file type, and metadata.",
    zh: "上传未完成。请检查权限、文件类型和 metadata。"
  },
  diagnosticsRestricted: { en: "Diagnostics restricted", zh: "诊断受限" },
  diagnosticsRestrictedHelp: {
    en: "This identity lacks diagnostics:read. Copy request_id for an engineer or administrator.",
    zh: "当前身份缺少 diagnostics:read。可复制 request_id 给有权限的工程或管理员排查。"
  },
  safeRetrievalTimeline: { en: "Safe retrieval timeline", zh: "安全检索时间线" },
  resolveDiagnostics: { en: "Resolve diagnostics", zh: "解析诊断" },
  diagnosticsError: {
    en: "Unable to resolve diagnostics. Raw query, prompt, and chunk content are not shown.",
    zh: "无法解析诊断摘要；不会显示 raw query、prompt 或 chunk content。"
  },
  citationReady: { en: "Citation ready", zh: "引用就绪" },
  citationReadyHelp: {
    en: "Select a citation and the system will call /sources/resolve again for authorization.",
    zh: "点击回答中的 citation 后，系统会重新调用 /sources/resolve 做二次授权。"
  },
  retrySource: { en: "Retry source", zh: "重试来源" },
  resolvingSource: { en: "Re-authorizing source", zh: "正在重新授权来源" },
  sourceUnavailable: { en: "Source unavailable", zh: "来源不可用" },
  sourceNotAvailable: {
    en: "The source excerpt cannot be re-authorized. The system does not reveal whether the resource exists, was deleted, or failed ACL checks.",
    zh: "该来源片段无法重新授权。系统不暴露资源是否存在、是否删除或 ACL 是否匹配。"
  },
  authorizedExcerpt: { en: "Authorized excerpt", zh: "已授权原文片段" },
  noExcerpt: {
    en: "The backend returned no excerpt; provider output is never used to fabricate sources.",
    zh: "后端未返回 excerpt；不使用 provider 输出补造来源。"
  },
  copyAnswerWithCitations: { en: "Copy answer with citations", zh: "复制带引用回答" },
  copyRequestId: { en: "Copy request_id", zh: "复制 request_id" },
  copyChunkId: { en: "Copy chunk_id", zh: "复制 chunk_id" },
  copied: { en: "Copied", zh: "已复制" },
  safeError: { en: "SAFE_ERROR", zh: "SAFE_ERROR" },
  noAnswerTitle: { en: "Cannot confirm from the authorized sources.", zh: "无法从当前授权资料确认。" },
  noAnswerHelp: {
    en: "Review retrieval scope, upload supporting material, or send request_id to an administrator.",
    zh: "你可以查看检索范围、上传补充资料，或把 request_id 发给管理员排查。"
  },
  viewDiagnostics: { en: "View diagnostics", zh: "查看诊断范围" },
  toolEventDefault: {
    en: "Controlled tool event summary. Raw arguments and output are hidden.",
    zh: "受控工具事件摘要，未展示 raw arguments/output。"
  },
  permissionRequired: { en: "Permission required", zh: "需要权限" },
  permissionRequiredHelp: {
    en: "This identity lacks the required permission. The UI does not reveal whether unauthorized resources exist.",
    zh: "当前身份缺少权限，界面不会展示未授权资源是否存在。"
  }
} satisfies Record<string, LocalizedText>;

export const navText = {
  ask: { label: { en: "Ask", zh: "问答" }, description: { en: "RAG Q&A", zh: "RAG 对话" } },
  knowledge: {
    label: { en: "Knowledge Base", zh: "知识库" },
    description: { en: "Import, versions, indexing", zh: "导入、版本和索引状态" }
  },
  review: { label: { en: "Review", zh: "审阅" }, description: { en: "Review, costs and support", zh: "审阅、成本与支持工单" } },
  diagnostics: {
    label: { en: "Diagnostics", zh: "诊断" },
    description: { en: "request_id trace review", zh: "request_id 检索复盘" }
  },
  eval: { label: { en: "Eval", zh: "评估" }, description: { en: "Quality regression", zh: "质量回归报告" } },
  audit: { label: { en: "Audit", zh: "审计" }, description: { en: "Security audit", zh: "安全审计摘要" } },
  agent: { label: { en: "Agent Runs", zh: "Agent 运行" }, description: { en: "Tool-call review", zh: "受控工具调用审阅" } },
  settings: { label: { en: "Settings", zh: "设置" }, description: { en: "Identity boundaries", zh: "身份和集成边界" } }
} satisfies Record<string, { label: LocalizedText; description: LocalizedText }>;

export const personaText = {
  employee: {
    label: { en: "Employee", zh: "员工" },
    summary: { en: "Ask and view authorized citations", zh: "提问、查看授权 citation 和 evidence" }
  },
  knowledge_manager: {
    label: { en: "Knowledge Manager", zh: "知识管理员" },
    summary: { en: "Import documents and monitor indexing", zh: "导入文件、管理版本和索引状态" }
  },
  ai_engineer: {
    label: { en: "AI Engineer", zh: "AI 工程师" },
    summary: { en: "Review retrieval, eval, and no-answer", zh: "复盘 retrieval、eval 和 no-answer" }
  },
  auditor: {
    label: { en: "Auditor", zh: "审计员" },
    summary: { en: "Review audit and governance queues", zh: "查看审计摘要和治理队列" }
  },
  platform_admin: {
    label: { en: "Platform Admin", zh: "平台管理员" },
    summary: { en: "Full governance and platform boundaries", zh: "完整治理入口和平台配置边界" }
  }
} satisfies Record<string, { label: LocalizedText; summary: LocalizedText }>;

export const agentText = {
  mode: { en: "Execution mode", zh: "执行模式" },
  oneShot: { en: "One-shot (default)", zh: "单次执行（默认）" },
  live: { en: "Live progress (SSE)", zh: "实时进度（SSE）" },
  durable: { en: "Durable approval and recovery", zh: "持久化审批与恢复" },
  liveHelp: { en: "Watch redacted tool progress. Only the validated final result contains an answer. No reconnect or replay is automatic.", zh: "查看脱敏后的工具进度，仅经验证的最终结果展示答案。不会自动重连或重放。" },
  durableHelp: { en: "Opt-in server feature; disabled by default. Start and resume finish within the original time budget. There is no background scheduler.", zh: "这是默认关闭的服务端可选功能。启动和恢复都受原始时间预算约束，没有后台调度器。" },
  privacy: { en: "Never enter credentials. Durable mode stores bounded private input and tool arguments on the server. This page does not save them in browser storage.", zh: "请勿输入凭据。持久化模式在服务端保存有界的私密输入及工具参数，本页面不会将它们写入浏览器存储。" },
  approvalTools: { en: "Require explicit approval for these built-in tools", zh: "以下内置工具需要明确审批" },
  approvalPolicy: { en: "This adds review requirements. A tool's server-side approval and permission policies always apply.", zh: "此选项增加审阅要求，工具的服务端审批及权限策略始终有效。" },
  stop: { en: "Stop waiting", zh: "停止等待" },
  pending: { en: "Request in progress", zh: "请求处理中" },
  progress: { en: "Agent progress", zh: "Agent 执行进度" },
  waitingFinal: { en: "Waiting for a validated final answer", zh: "等待经验证的最终答案" },
  recoverTitle: { en: "Read an existing durable run", zh: "读取已有持久化运行" },
  recoverHelp: { en: "Paste a run ID you own. Reading never resumes execution. Copy the ID before leaving; recovery data expires under server retention policy.", zh: "粘贴属于当前身份的运行 ID。读取不会恢复执行；离开前请复制 ID，恢复数据会按服务端保留策略过期。" },
  runId: { en: "Durable run ID", zh: "持久化运行 ID" },
  read: { en: "Read run", zh: "读取运行" },
  refresh: { en: "Refresh run", zh: "刷新运行" },
  resume: { en: "Resume run", zh: "恢复运行" },
  approve: { en: "Approve exact action", zh: "批准此精确操作" },
  reject: { en: "Reject and cancel run", zh: "拒绝并取消运行" },
  cancel: { en: "Cancel durable run", zh: "取消持久化运行" },
  copyRunId: { en: "Copy run ID", zh: "复制运行 ID" },
  latest: { en: "Last verified server state", zh: "最近验证的服务端状态" },
  stale: { en: "This is a previous snapshot. Refresh before taking another action.", zh: "当前是历史快照，请刷新后再操作。" },
  approvalTitle: { en: "Review the exact requested action", zh: "审阅精确的请求操作" },
  approvalHelp: { en: "Approval applies only to this tool, these arguments, this definition, and this revision. A fresh server read must still match before the decision is sent.", zh: "审批仅适用于此工具、参数、定义及版本。发送决定前会再次读取服务端状态，完全一致才可继续。" },
  arguments: { en: "Exact tool arguments", zh: "精确工具参数" },
  deadline: { en: "Original deadline (UTC)", zh: "原始截止时间（UTC）" },
  approvalExpiry: { en: "Approval expiry (UTC)", zh: "审批过期时间（UTC）" },
  stepsUsed: { en: "Decisions consumed", zh: "已消耗决策次数" },
  toolsUsed: { en: "Tool calls consumed", zh: "已消耗工具调用次数" },
  budgetHelp: { en: "Consumed counters and the original deadline survive recovery. Time spent waiting for approval counts toward that deadline.", zh: "恢复不会重置已消耗次数及原始截止时间，等待审批的时间也计入预算。" },
  expired: { en: "The original deadline or approval has expired. This view cannot approve or resume the run. Refresh to check its state.", zh: "原始截止时间或审批已过期，本页面不能批准或恢复此运行。请刷新检查状态。" },
  intervention: { en: "A tool may have taken effect, but its result is unknown. An operator must reconcile external state before starting a new run. This run cannot be resumed.", zh: "工具可能已生效，但结果未知。操作人员必须先核对外部状态再启动新运行，此运行无法恢复。" },
  terminal: { en: "This run is terminal and cannot be resumed.", zh: "此运行已进入终态，无法恢复。" },
  lease: { en: "A running snapshot may be recovered only after the prior server lease expires. Resume is explicit; budgets are never reset.", zh: "运行中快照仅能在前一服务端租约到期后恢复。恢复必须明确发起，预算不会重置。" },
  disabled: { en: "Durable Agent is disabled on this server. An operator must prepare its storage and enable it deliberately. One-shot and Live modes remain available.", zh: "此服务端尚未启用持久化 Agent，需要操作人员先准备存储并明确启用。仍可使用单次执行与实时模式。" },
  disabledRecovery: { en: "Durable Agent is disabled on this server. This cannot reconcile an earlier start. Keep its key until recovery is available or explicitly acknowledge unresolved effects before discarding the local reference.", zh: "此服务端尚未启用持久化 Agent，无法核对此前的启动结果。请保留恢复键直到可以读取，或在明确承认影响未解决后丢弃本地引用。" },
  conflict: { en: "The run changed or another request owns it. Refresh and review the latest state; the previous decision was not retried.", zh: "运行状态已变化，或另一个请求正持有它。请刷新并审阅最新状态，先前决定不会自动重试。" },
  changed: { en: "The server state changed after you reviewed it. Review this updated action and choose again; no decision was sent.", zh: "服务端状态在审阅后发生变化。请重新审阅并选择；尚未发送审批或恢复决定。" },
  streamInterrupted: { en: "The live connection was closed and disconnect cancellation was requested. Server-side cancellation persistence is not confirmed.", zh: "实时连接已关闭，并通过断连请求取消；尚未确认服务端已持久化取消状态。" },
  requestInterrupted: { en: "Waiting stopped locally. The server outcome is not confirmed.", zh: "本地已停止等待，服务端结果尚未确认。" },
  startUnknown: { en: "The start outcome is unknown and no run ID was received. Use the retained startup key to read the server state explicitly. Do not repeat a possible side effect; reading does not resume or approve a run.", zh: "启动结果未知且未收到运行 ID。请使用保留的启动恢复键明确读取服务端状态。请勿重复可能产生副作用的操作；读取不会恢复运行或批准操作。" },
  startupKey: { en: "Startup recovery key", zh: "启动恢复键" },
  startupKeyHelp: { en: "Generated securely before dispatch and kept only in memory. Copy this key before leaving or changing identity; this page does not save it. It is scoped to the current tenant and user and never grants access or approval.", zh: "此键在发送前安全生成，仅保存在内存中。离开或更换身份前请复制；页面不会保存它。此键仅适用于当前租户及用户，不能授予访问权限或审批。" },
  copyStartupKey: { en: "Copy startup key", zh: "复制启动恢复键" },
  readStartupKey: { en: "Read by retained startup key", zh: "使用保留的启动恢复键读取" },
  savedStartupKey: { en: "Saved startup recovery key (UUID4)", zh: "已保存的启动恢复键（UUID4）" },
  readSavedKey: { en: "Read by saved startup key", zh: "使用已保存的启动恢复键读取" },
  recoverKeyHelp: { en: "If no run ID arrived, enter the startup UUID4 you copied. This only reads current-owner state; it never starts, retries, resumes or approves execution. A key from another identity does not change your authentication.", zh: "若未收到运行 ID，请输入之前复制的 UUID4 启动恢复键。这只会读取当前身份拥有的状态，不会启动、重试、恢复或批准执行。其他身份的恢复键不会改变当前认证。" },
  unresolvedStart: { en: "A new start is blocked until this reference is resolved or you explicitly discard it and acknowledge unresolved server effects.", zh: "在此恢复引用得到确认前，不能启动新运行；也可在明确承认服务端影响仍未解决后丢弃此本地引用。" },
  discardAcknowledgement: { en: "I understand server effects remain unresolved and discarding this reference does not cancel the run", zh: "我理解服务端影响仍未解决，丢弃此引用不会取消运行" },
  discardStartupKey: { en: "Discard local startup reference", zh: "丢弃本地启动引用" },
  startDiscarded: { en: "The local startup reference was discarded. No server cancellation was requested or confirmed. Reconcile possible effects before deliberately starting another run.", zh: "已丢弃本地启动引用，未请求或确认服务端取消。明确启动另一次运行前，请先核对可能已产生的影响。" },
  recoveryNotFound: { en: "No run was found for this key under the current identity. The original request may still commit later; 404 does not prove the start failed. Keep the key and read again explicitly, or ask an operator to reconcile possible effects.", zh: "当前身份下未找到此恢复键对应的运行。原请求仍可能稍后提交；404 不能证明启动失败。请保留恢复键并明确再次读取，或请操作人员核对可能产生的影响。" },
  secureRandomUnavailable: { en: "Secure key generation is unavailable. No durable start was sent. Use a browser with secure Web Crypto support.", zh: "无法安全生成恢复键，未发送持久化启动请求。请使用支持安全 Web Crypto 的浏览器。" },
  invalidStartupKey: { en: "Enter a valid UUID4 startup recovery key. No recovery request was sent.", zh: "请输入有效的 UUID4 启动恢复键，未发送恢复请求。" },
  durableUnknown: { en: "The server outcome is not confirmed. Refresh this run before deciding whether to resume, approve, or cancel.", zh: "服务端结果尚未确认。请先刷新此运行，再决定是否恢复、审批或取消。" },
  cancelConfirmed: { en: "The server confirmed the durable run is cancelled.", zh: "服务端已确认此持久化运行被取消。" },
  sourceRefs: { en: "Validated source references", zh: "已验证的来源引用" },
  closeSource: { en: "Close source", zh: "关闭来源" },
  inputHelp: { en: "Up to 4,000 characters. Decisions: 1–20; tool calls: 0–20; timeout: 1–120 seconds.", zh: "最多 4,000 字符。决策次数 1–20，工具调用次数 0–20，超时 1–120 秒。" },
  activeRun: { en: "Finish or cancel the current durable run before starting or reading another one.", zh: "请先完成或取消当前持久化运行，再启动或读取另一个运行。" }
} satisfies Record<string, LocalizedText>;

export const agentStatusText = {
  running: { en: "Running", zh: "运行中" },
  awaiting_approval: { en: "Awaiting approval", zh: "等待审批" },
  completed: { en: "Completed", zh: "已完成" },
  stopped: { en: "Stopped", zh: "已停止" },
  failed: { en: "Failed", zh: "失败" },
  cancelled: { en: "Cancelled", zh: "已取消" },
  intervention_required: { en: "Operator intervention required", zh: "需要操作人员介入" }
} satisfies Record<string, LocalizedText>;
