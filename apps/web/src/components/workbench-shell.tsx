"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  ClipboardCheck,
  ExternalLink,
  FileUp,
  LogOut,
  Send,
  X
} from "lucide-react";
import { useMemo, useState } from "react";
import { streamChat } from "@/lib/api/client";
import type { Citation, SseEvent, ToolEvent, UploadDocumentResult } from "@/lib/api/types";
import {
  NAV_ITEMS,
  defaultSurfaceFor,
  hasPermission,
  type AuthSession,
  type SurfaceKey
} from "@/lib/auth";
import { DiagnosticsPanel } from "./diagnostics-panel";
import { EvidencePanel } from "./evidence-panel";
import { KnowledgeBasePanel, QuickImportDrawerContent } from "./knowledge-base";
import {
  CitationChip,
  CopyIdButton,
  NoAnswerPanel,
  PermissionNotice,
  SafeErrorBanner,
  ScopeBadge,
  StatusPill,
  ToolEventRow
} from "./primitives";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
  requestId?: string | null;
  traceId?: string | null;
  isFinal: boolean;
  status?: "answered" | "no_answer" | "error";
  toolEvents: ToolEvent[];
};

export function WorkbenchShell({
  auth,
  onSignOut
}: Readonly<{ auth: AuthSession; onSignOut: () => void }>) {
  const [activeSurface, setActiveSurface] = useState<SurfaceKey>(() => defaultSurfaceFor(auth));
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [inspectorTab, setInspectorTab] = useState<"evidence" | "diagnostics">("evidence");
  const [currentRequestId, setCurrentRequestId] = useState<string | null>(null);
  const [currentTraceId, setCurrentTraceId] = useState<string | null>(null);
  const [quickImportResult, setQuickImportResult] = useState<UploadDocumentResult | null>(null);

  const visibleNav = useMemo(
    () => NAV_ITEMS.filter((item) => !item.sensitive || hasPermission(auth, item.permission)),
    [auth]
  );

  return (
    <main className="workbench" data-testid="workbench-shell">
      <aside className="sidebar" aria-label="Workbench navigation">
        <div className="brand">
          <span className="brand-mark">R</span>
          <span>RAG Workbench</span>
        </div>
        <div className="scope-stack">
          <span className="scope-label">Current identity</span>
          <strong>{auth.label}</strong>
          <ScopeBadge label={`${auth.tenantId ?? "tenant from JWT"} / ${auth.department ?? "scope from backend"}`} />
          <span className="id-text">user: {auth.userId ?? "JWT subject"}</span>
        </div>
        <nav className="nav-section">
          {visibleNav.map((item) => {
            const Icon = item.icon;
            const permitted = hasPermission(auth, item.permission);
            return (
              <button
                key={item.key}
                type="button"
                className={`nav-button ${permitted ? "" : "is-disabled"}`}
                aria-current={activeSurface === item.key ? "page" : undefined}
                title={permitted ? item.description : `需要 ${item.permission}`}
                onClick={() => setActiveSurface(item.key)}
              >
                <Icon aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <button type="button" className="ghost-button" onClick={onSignOut}>
          <LogOut aria-hidden="true" />
          Sign out
        </button>
      </aside>

      <section className="main-panel">
        <div className="topbar">
          <div>
            <h1 className="surface-title">Enterprise Knowledge Operations</h1>
            <p className="muted">主前端是产品界面；Open WebUI 作为兼容入口和演示入口保留。</p>
          </div>
          <div className="actions-row">
            <QuickImportButton auth={auth} onUploaded={setQuickImportResult} />
            <a className="secondary-button" href="/sidecar" target="_blank" rel="noreferrer">
              <ExternalLink aria-hidden="true" />
              Sidecar
            </a>
          </div>
        </div>

        {quickImportResult !== null && (
          <div className="surface">
            <StatusPill tone="index">Upload queued</StatusPill>
            <span className="id-text">job_id: {quickImportResult.job_id}</span>
          </div>
        )}

        {activeSurface === "ask" && (
          <AskPanel
            auth={auth}
            onOpenCitation={(citation) => {
              setSelectedCitation(citation);
              setInspectorTab("evidence");
            }}
            onRequestContext={(requestId, traceId) => {
              setCurrentRequestId(requestId);
              setCurrentTraceId(traceId);
            }}
            onOpenDiagnostics={() => {
              setInspectorTab("diagnostics");
            }}
          />
        )}
        {activeSurface === "knowledge" && <KnowledgeBasePanel auth={auth} />}
        {activeSurface === "diagnostics" && (
          <section className="surface">
            <DiagnosticsPanel
              auth={auth}
              requestId={currentRequestId}
              traceId={currentTraceId}
              canRead={hasPermission(auth, "diagnostics:read")}
            />
          </section>
        )}
        {["review", "eval", "audit", "agent", "settings"].includes(activeSurface) && (
          <GovernancePlaceholder surface={activeSurface} auth={auth} />
        )}
      </section>

      <aside className="inspector-panel" aria-label="Evidence and diagnostics panel">
        <div className="tabs" role="tablist" aria-label="Inspector tabs">
          <button
            type="button"
            className="tab"
            role="tab"
            aria-selected={inspectorTab === "evidence"}
            onClick={() => setInspectorTab("evidence")}
          >
            Evidence
          </button>
          <button
            type="button"
            className="tab"
            role="tab"
            aria-selected={inspectorTab === "diagnostics"}
            onClick={() => setInspectorTab("diagnostics")}
          >
            Diagnostics
          </button>
        </div>
        {inspectorTab === "evidence" ? (
          <EvidencePanel auth={auth} citation={selectedCitation} />
        ) : (
          <DiagnosticsPanel
            auth={auth}
            requestId={currentRequestId}
            traceId={currentTraceId}
            canRead={hasPermission(auth, "diagnostics:read")}
          />
        )}
      </aside>
    </main>
  );
}

function AskPanel({
  auth,
  onOpenCitation,
  onRequestContext,
  onOpenDiagnostics
}: Readonly<{
  auth: AuthSession;
  onOpenCitation: (citation: Citation) => void;
  onRequestContext: (requestId: string | null, traceId: string | null) => void;
  onOpenDiagnostics: () => void;
}>) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canAsk = hasPermission(auth, "retrieval:query");

  async function submit() {
    if (!canAsk || question.trim().length === 0 || isStreaming) {
      return;
    }
    const userText = question.trim();
    setQuestion("");
    setError(null);
    const assistantId = crypto.randomUUID();
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: "user",
        text: userText,
        citations: [],
        isFinal: true,
        toolEvents: []
      },
      {
        id: assistantId,
        role: "assistant",
        text: "",
        citations: [],
        isFinal: false,
        toolEvents: []
      }
    ]);
    setIsStreaming(true);

    try {
      for await (const event of streamChat(auth, userText)) {
        applySseEvent(assistantId, event, setMessages, onRequestContext);
      }
    } catch (streamError) {
      setError(streamError instanceof Error ? streamError.message : "Chat stream failed.");
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, isFinal: true, status: "error" } : message
        )
      );
    } finally {
      setIsStreaming(false);
    }
  }

  return (
    <section className="surface" aria-labelledby="ask-title">
      <div className="surface-header">
        <div>
          <h2 id="ask-title" className="surface-title">
            Ask with citations
          </h2>
          <p className="muted">只提交问题和可选收窄范围；前端不构造 tenant、roles 或 provider prompt。</p>
        </div>
        <StatusPill tone={canAsk ? "source" : "danger"}>
          {canAsk ? "retrieval:query" : "AUTH_CONTEXT_REQUIRED"}
        </StatusPill>
      </div>

      {!canAsk && <PermissionNotice permission="retrieval:query" />}
      {error !== null && <SafeErrorBanner message={error} />}

      <div className="message-list" aria-live="polite">
        {messages.length === 0 && (
          <div className="message answer">
            <p>请输入企业知识问题。回答完成前，“Copy answer with citations” 会保持禁用。</p>
            <div className="chip-row">
              <StatusPill tone="source">citation locked after final</StatusPill>
              <StatusPill tone="index">request_id required for diagnostics</StatusPill>
            </div>
            <div className="actions-row">
              <CopyIdButton value="" label="Copy answer with citations" disabled />
            </div>
          </div>
        )}
        {messages.map((message) => (
          <article key={message.id} className={`message ${message.role}`}>
            <div className="message-meta">
              <strong>{message.role === "user" ? "You" : "Assistant"}</strong>
              {message.requestId !== undefined && message.requestId !== null && (
                <span className="id-text">request_id: {message.requestId}</span>
              )}
            </div>
            <p>{message.text || (message.isFinal ? "无法从当前授权资料确认。" : "Streaming tokens...")}</p>
            {message.status === "no_answer" && (
              <NoAnswerPanel requestId={message.requestId} onDiagnostics={onOpenDiagnostics} />
            )}
            {message.toolEvents.map((event, index) => (
              <ToolEventRow key={`${message.id}-${event.tool_name}-${index}`} event={event} />
            ))}
            <div className="chip-row">
              {message.citations.map((citation) => (
                <CitationChip
                  key={`${citation.document_id}-${citation.version_id ?? ""}-${citation.chunk_id}`}
                  citation={citation}
                  onOpen={onOpenCitation}
                />
              ))}
            </div>
            {message.role === "assistant" && (
              <div className="actions-row">
                <CopyIdButton
                  value={message.isFinal ? answerWithCitations(message) : ""}
                  label="Copy answer with citations"
                  disabled={!message.isFinal}
                />
                <CopyIdButton value={message.requestId} label="Copy request_id" />
              </div>
            )}
          </article>
        ))}
      </div>

      <div className="composer">
        <textarea
          className="text-area"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="询问当前授权知识库中的制度、合同、规范或研发知识..."
          disabled={!canAsk}
        />
        <div className="actions-row">
          <button
            type="button"
            className="primary-button"
            onClick={() => void submit()}
            disabled={!canAsk || question.trim().length === 0 || isStreaming}
          >
            <Send aria-hidden="true" />
            Ask
          </button>
          <span className="muted">no-answer 是成功状态，不补造来源。</span>
        </div>
      </div>
    </section>
  );
}

function QuickImportButton({
  auth,
  onUploaded
}: Readonly<{ auth: AuthSession; onUploaded: (result: UploadDocumentResult) => void }>) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button type="button" className="secondary-button" disabled={!hasPermission(auth, "document:upload")}>
          <FileUp aria-hidden="true" />
          Import
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content">
          <div className="surface-header">
            <Dialog.Title className="surface-title">Quick import</Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="icon-button" aria-label="Close import drawer">
                <X aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="muted">
            快捷导入只包含常用字段；高级 metadata 和版本管理在 Knowledge Base 页面处理。
          </Dialog.Description>
          <QuickImportDrawerContent auth={auth} onUploaded={onUploaded} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function GovernancePlaceholder({
  surface,
  auth
}: Readonly<{ surface: SurfaceKey; auth: AuthSession }>) {
  const item = NAV_ITEMS.find((candidate) => candidate.key === surface);
  const permitted = hasPermission(auth, item?.permission);

  if (!permitted && item?.permission !== undefined) {
    return <PermissionNotice permission={item.permission} />;
  }

  return (
    <section className="surface">
      <div className="surface-header">
        <div>
          <h2 className="surface-title">{item?.label ?? surface}</h2>
          <p className="muted">
            本 story 固定稳定入口和安全空状态；未直接接入的治理明细继续跳转现有 /governance。
          </p>
        </div>
        <a className="primary-button" href="/governance" target="_blank" rel="noreferrer">
          <ExternalLink aria-hidden="true" />
          Open governance
        </a>
      </div>
      <div className="tool-row">
        <ClipboardCheck aria-hidden="true" />
        <strong>Backend facts only</strong>
        <span className="muted">
          不展示假数据、不展示 raw query、prompt、chunk content、SQL、vectors、provider payload 或 secrets。
        </span>
      </div>
    </section>
  );
}

function applySseEvent(
  assistantId: string,
  event: SseEvent,
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  onRequestContext: (requestId: string | null, traceId: string | null) => void
) {
  setMessages((current) =>
    current.map((message) => {
      if (message.id !== assistantId) {
        return message;
      }
      if (event.type === "token") {
        return { ...message, text: message.text + (event.data.token ?? event.data.text ?? "") };
      }
      if (event.type === "citation") {
        const citations = Array.isArray(event.data) ? event.data : [event.data];
        return { ...message, citations: dedupeCitations([...message.citations, ...citations]) };
      }
      if (event.type === "tool_call" || event.type === "tool_result") {
        return { ...message, toolEvents: [...message.toolEvents, event.data] };
      }
      if (event.type === "error") {
        onRequestContext(event.data.request_id ?? null, event.data.trace_id ?? null);
        return {
          ...message,
          requestId: event.data.request_id,
          traceId: event.data.trace_id,
          status: "error",
          isFinal: true
        };
      }
      onRequestContext(event.data.request_id ?? null, event.data.trace_id ?? null);
      return {
        ...message,
        text: event.data.answer ?? message.text,
        citations: dedupeCitations(event.data.citations ?? message.citations),
        requestId: event.data.request_id ?? message.requestId,
        traceId: event.data.trace_id ?? message.traceId,
        status: event.data.status ?? (event.data.citations?.length === 0 ? "no_answer" : "answered"),
        isFinal: true
      };
    })
  );
}

function dedupeCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  return citations.filter((citation) => {
    const key = `${citation.document_id}:${citation.version_id ?? ""}:${citation.chunk_id}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function answerWithCitations(message: ChatMessage): string {
  const citationLines = message.citations.map(
    (citation, index) => `[${index + 1}] ${citation.document_id} / ${citation.version_id ?? "-"} / ${citation.chunk_id}`
  );
  return [message.text, "", "Citations:", ...citationLines].join("\n");
}
