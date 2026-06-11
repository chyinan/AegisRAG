"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  CornerDownLeft,
  ExternalLink,
  FileUp,
  Lock,
  LogOut,
  ShieldCheck,
  Sparkles,
  X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { loadChatHistory, streamChat } from "@/lib/api/client";
import type { ChatHistoryMessage, Citation, SseEvent, ToolEvent, UploadDocumentResult } from "@/lib/api/types";
import {
  NAV_ITEMS,
  PERSONAS,
  defaultSurfaceFor,
  hasPermission,
  type AuthSession,
  type PersonaKey,
  type SurfaceKey
} from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { navText, personaText, text, uiText } from "@/lib/i18n";
import { AnswerMarkdown } from "./answer-markdown";
import { AuditPanel } from "./audit-panel";
import { DiagnosticsPanel } from "./diagnostics-panel";
import { EvidencePanel } from "./evidence-panel";
import { AgentPanel, EvalPanel, ReviewPanel, SettingsPanel } from "./governance-panels";
import { KnowledgeBasePanel, QuickImportDrawerContent } from "./knowledge-base";
import { LanguageSelect } from "./language-select";
import {
  CitationChip,
  CopyIdButton,
  NoAnswerPanel,
  PermissionNotice,
  SafeErrorBanner,
  citationSourceLabel,
  ScopeBadge,
  StatusPill,
  ToolEventRow
} from "./primitives";
import { Button } from "./ui/button";
import { Card, CardHeader, CardInset } from "./ui/card";
import { TabsList, TabsTrigger } from "./ui/tabs";
import { Textarea } from "./ui/textarea";

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

export type CitationDisplayGroup = {
  key: string;
  primary: Citation;
  label: string;
  count: number;
};

export function WorkbenchShell({
  auth,
  language,
  onLanguageChange,
  onSignOut
}: Readonly<{
  auth: AuthSession;
  language: Language;
  onLanguageChange: (language: Language) => void;
  onSignOut: () => void;
}>) {
  const [activeSurface, setActiveSurface] = useState<SurfaceKey>(() => defaultSurfaceFor(auth));
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [selectedCitationShouldResolve, setSelectedCitationShouldResolve] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<"evidence" | "diagnostics">("evidence");
  const [currentRequestId, setCurrentRequestId] = useState<string | null>(null);
  const [currentTraceId, setCurrentTraceId] = useState<string | null>(null);
  const [quickImportResult, setQuickImportResult] = useState<UploadDocumentResult | null>(null);

  const visibleNav = useMemo(
    () => NAV_ITEMS.filter((item) => !item.sensitive || hasPermission(auth, item.permission)),
    [auth]
  );
  const personaKey = (Object.keys(PERSONAS) as PersonaKey[]).find((key) => PERSONAS[key].userId === auth.userId);
  const identityLabel = personaKey !== undefined ? text(personaText[personaKey].label, language) : auth.label;

  return (
    <main className="workbench" data-testid="workbench-shell">
      <aside className="sidebar" aria-label="Workbench navigation">
        <div className="brand">
          <span>
            AegisRAG
            <small>Enterprise Workbench</small>
          </span>
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
                title={permitted ? text(navText[item.key].description, language) : `Requires ${item.permission}`}
                onClick={() => setActiveSurface(item.key)}
              >
                <Icon aria-hidden="true" />
                <span>
                  {text(navText[item.key].label, language)}
                  <small>{text(navText[item.key].description, language)}</small>
                </span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-account">
          <div className="account-copy">
            <span className="scope-label">{text(uiText.currentIdentity, language)}</span>
            <strong>{identityLabel}</strong>
          </div>
          <Button type="button" variant="icon" size="icon" onClick={onSignOut} aria-label={text(uiText.signOut, language)}>
            <LogOut aria-hidden="true" />
          </Button>
          <div className="account-meta">
            <ScopeBadge label={`${auth.tenantId ?? "tenant from JWT"} / ${auth.department ?? "scope from backend"}`} />
            <span className="id-text account-id" title={`user: ${auth.userId ?? "JWT subject"}`}>
              user: {auth.userId ?? "JWT subject"}
            </span>
          </div>
        </div>
      </aside>

      <section className="main-panel">
        <div className="topbar">
          <div>
            <h1 className="surface-title">{text(uiText.mainTitle, language)}</h1>
            <p className="muted">{text(uiText.mainSubtitle, language)}</p>
          </div>
          <div className="actions-row">
            <LanguageSelect language={language} onLanguageChange={onLanguageChange} />
            <QuickImportButton auth={auth} language={language} onUploaded={setQuickImportResult} />
            <Button asChild variant="secondary">
              <a href="/sidecar" target="_blank" rel="noreferrer">
                <ExternalLink aria-hidden="true" />
                {text(uiText.sidecar, language)}
              </a>
            </Button>
          </div>
          <div className="topbar-metrics" aria-label="Safety boundaries">
            <span>
              <ShieldCheck aria-hidden="true" />
              {text(uiText.rbacMetric, language)}
            </span>
            <span>
              <Lock aria-hidden="true" />
              {text(uiText.citationMetric, language)}
            </span>
            <span>
              <Sparkles aria-hidden="true" />
              {text(uiText.streamingMetric, language)}
            </span>
          </div>
        </div>

        {quickImportResult !== null && (
          <Card>
            <StatusPill tone="index">{text(uiText.uploadQueued, language)}</StatusPill>
            <span className="id-text">job_id: {quickImportResult.job_id}</span>
          </Card>
        )}

        {activeSurface === "ask" && (
          <AskPanel
            auth={auth}
            language={language}
            onOpenCitation={(citation, shouldResolve = true) => {
              setSelectedCitation(citation);
              setSelectedCitationShouldResolve(shouldResolve);
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
        {activeSurface === "knowledge" && <KnowledgeBasePanel auth={auth} language={language} />}
        {activeSurface === "diagnostics" && (
          <Card>
            <DiagnosticsPanel
              auth={auth}
              language={language}
              requestId={currentRequestId}
              traceId={currentTraceId}
              canRead={hasPermission(auth, "diagnostics:read")}
            />
          </Card>
        )}
        {activeSurface === "audit" && (
          <AuditPanel auth={auth} language={language} canRead={hasPermission(auth, "audit:read")} />
        )}
        {activeSurface === "review" && (
          <ReviewPanel auth={auth} language={language} canRead={hasPermission(auth, "review:read")} />
        )}
        {activeSurface === "eval" && (
          <EvalPanel auth={auth} language={language} canRead={hasPermission(auth, "eval:read")} />
        )}
        {activeSurface === "agent" && (
          <AgentPanel auth={auth} language={language} canRun={hasPermission(auth, "agent:run")} />
        )}
        {activeSurface === "settings" && (
          hasPermission(auth, "admin:settings") ? (
            <SettingsPanel auth={auth} language={language} />
          ) : (
            <PermissionNotice permission="admin:settings" language={language} />
          )
        )}
      </section>

      <aside className="inspector-panel" aria-label="Evidence and diagnostics panel">
        <TabsList role="tablist" aria-label="Inspector tabs">
          <TabsTrigger
            type="button"
            role="tab"
            aria-selected={inspectorTab === "evidence"}
            onClick={() => setInspectorTab("evidence")}
          >
            {text(uiText.evidence, language)}
          </TabsTrigger>
          <TabsTrigger
            type="button"
            role="tab"
            aria-selected={inspectorTab === "diagnostics"}
            onClick={() => setInspectorTab("diagnostics")}
          >
            {text(uiText.diagnostics, language)}
          </TabsTrigger>
        </TabsList>
        {inspectorTab === "evidence" ? (
          <EvidencePanel
            auth={auth}
            citation={selectedCitation}
            language={language}
            shouldResolve={selectedCitationShouldResolve}
          />
        ) : (
          <DiagnosticsPanel
            auth={auth}
            language={language}
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
  language,
  onOpenCitation,
  onRequestContext,
  onOpenDiagnostics
}: Readonly<{
  auth: AuthSession;
  language: Language;
  onOpenCitation: (citation: Citation, shouldResolve?: boolean) => void;
  onRequestContext: (requestId: string | null, traceId: string | null) => void;
  onOpenDiagnostics: () => void;
}>) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyRestored, setHistoryRestored] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canAsk = hasPermission(auth, "retrieval:query");
  const storageKey = useMemo(() => chatSessionStorageKey(auth), [auth.tenantId, auth.userId]);

  useEffect(() => {
    if (!canAsk) {
      return;
    }
    const storedSessionId = readStoredSessionId(storageKey);
    if (storedSessionId === null) {
      return;
    }
    setSessionId(storedSessionId);
    let cancelled = false;
    void loadChatHistory(auth, storedSessionId)
      .then((history) => {
        if (cancelled || history.session_id !== storedSessionId || history.messages.length === 0) {
          return;
        }
        setMessages(history.messages.map(historyMessageToChatMessage));
        setHistoryRestored(true);
        setHistoryError(null);
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        removeStoredSessionId(storageKey);
        setSessionId(null);
        setHistoryRestored(false);
        setHistoryError(text(uiText.historyUnavailable, language));
      });
    return () => {
      cancelled = true;
    };
  }, [auth, canAsk, language, storageKey]);

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
    const activeSessionId = sessionId;

    try {
      for await (const event of streamChat(auth, userText, activeSessionId)) {
        applySseEvent(assistantId, event, setMessages, onRequestContext, (nextSessionId) => {
          setSessionId(nextSessionId);
          writeStoredSessionId(storageKey, nextSessionId);
        });
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

  function startNewConversation() {
    setMessages([]);
    setSessionId(null);
    setHistoryRestored(false);
    setHistoryError(null);
    removeStoredSessionId(storageKey);
    onRequestContext(null, null);
  }

  return (
    <Card className="ask-surface" aria-labelledby="ask-title">
      <CardHeader>
        <div>
          <h2 id="ask-title" className="surface-title">
            {text(uiText.askTitle, language)}
          </h2>
          <p className="muted">{text(uiText.askHelp, language)}</p>
        </div>
        <StatusPill tone={canAsk ? "source" : "danger"}>
          {canAsk ? "retrieval:query" : "AUTH_CONTEXT_REQUIRED"}
        </StatusPill>
      </CardHeader>

      <CardInset className="conversation-history-bar">
        <div>
          <span className="scope-label">{text(uiText.conversationHistory, language)}</span>
          <div className="chip-row">
            <span className="id-text">{text(uiText.currentSession, language)}: {sessionId ?? "new"}</span>
            {historyRestored && <StatusPill tone="source">{text(uiText.restoredHistory, language)}</StatusPill>}
          </div>
          {historyError !== null && <span className="muted">{historyError}</span>}
        </div>
        <Button type="button" variant="secondary" onClick={startNewConversation} disabled={isStreaming}>
          {text(uiText.newConversation, language)}
        </Button>
      </CardInset>

      {!canAsk && <PermissionNotice permission="retrieval:query" language={language} />}
      {error !== null && <SafeErrorBanner message={error} />}

      <div className="message-list" aria-live="polite">
        {messages.length === 0 && (
          <CardInset className="bg-[#f7fbfa] shadow-[inset_3px_0_0_var(--source),inset_0_0_0_1px_rgb(20_122_103_/_0.12)]">
            <p>{text(uiText.askEmpty, language)}</p>
            <div className="chip-row">
              <StatusPill tone="source">citation locked after final</StatusPill>
              <StatusPill tone="index">request_id required for diagnostics</StatusPill>
            </div>
            <div className="actions-row">
              <CopyIdButton value="" label={text(uiText.copyAnswerWithCitations, language)} disabled language={language} />
            </div>
          </CardInset>
        )}
        {messages.map((message) => (
          <CardInset
            key={message.id}
            as="article"
            className={
              message.role === "assistant"
                ? "bg-[#f7fbfa] shadow-[inset_3px_0_0_var(--source),inset_0_0_0_1px_rgb(20_122_103_/_0.12)]"
                : "bg-[#f5f7fb]"
            }
          >
            <div className="message-meta">
              <strong>{message.role === "user" ? "You" : "Assistant"}</strong>
              {message.requestId !== undefined && message.requestId !== null && (
                <span className="id-text">request_id: {message.requestId}</span>
              )}
            </div>
            {message.role === "assistant" ? (
              <AnswerMarkdown
                answer={message.text || (message.isFinal ? text(uiText.cannotConfirm, language) : "Streaming tokens...")}
                citations={message.citations}
                onOpenCitation={onOpenCitation}
                language={language}
              />
            ) : (
              <p>{message.text}</p>
            )}
            {message.status === "no_answer" && (
              <NoAnswerPanel requestId={message.requestId} onDiagnostics={onOpenDiagnostics} language={language} />
            )}
            {message.toolEvents.map((event, index) => (
              <ToolEventRow key={`${message.id}-${event.tool_name}-${index}`} event={event} language={language} />
            ))}
            <div className="chip-row">
              {groupCitationsForDisplay(message.citations).map((group) => (
                <CitationChip
                  key={group.key}
                  citation={group.primary}
                  label={group.label}
                  count={group.count}
                  onOpen={onOpenCitation}
                  language={language}
                />
              ))}
            </div>
            {message.role === "assistant" && (
              <div className="actions-row">
                <CopyIdButton
                  value={message.isFinal ? answerWithCitations(message) : ""}
                  label={text(uiText.copyAnswerWithCitations, language)}
                  disabled={!message.isFinal}
                  language={language}
                />
                <CopyIdButton value={message.requestId} label={text(uiText.copyRequestId, language)} language={language} />
              </div>
            )}
          </CardInset>
        ))}
      </div>

      <div className="composer">
        <div className="command-center">
          <Textarea
            className="command-input"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={text(uiText.askPlaceholder, language)}
            disabled={!canAsk}
          />
          <div className="command-footer">
            <div className="chip-row">
              <StatusPill tone={canAsk ? "source" : "danger"}>
                {canAsk ? "retrieval:query" : "AUTH_CONTEXT_REQUIRED"}
              </StatusPill>
              <StatusPill tone="index">citation locked until final</StatusPill>
            </div>
            <Button
              type="button"
              variant="primary"
              size="icon"
              className="size-[34px] rounded-lg"
              onClick={() => void submit()}
              disabled={!canAsk || question.trim().length === 0 || isStreaming}
              aria-label="Ask"
            >
              <CornerDownLeft aria-hidden="true" />
            </Button>
          </div>
        </div>
        <div className="composer-note">
          <span className="muted">{text(uiText.noAnswerNote, language)}</span>
        </div>
      </div>
    </Card>
  );
}

function QuickImportButton({
  auth,
  language,
  onUploaded
}: Readonly<{ auth: AuthSession; language: Language; onUploaded: (result: UploadDocumentResult) => void }>) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <Button type="button" variant="secondary" disabled={!hasPermission(auth, "document:upload")}>
          <FileUp aria-hidden="true" />
          {text(uiText.quickImport, language)}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content">
          <CardHeader>
            <Dialog.Title className="surface-title">{text(uiText.quickImport, language)}</Dialog.Title>
            <Dialog.Close asChild>
              <Button type="button" variant="icon" size="icon" aria-label={text(uiText.closeImport, language)}>
                <X aria-hidden="true" />
              </Button>
            </Dialog.Close>
          </CardHeader>
          <Dialog.Description className="muted">
            {text(uiText.quickImportDescription, language)}
          </Dialog.Description>
          <QuickImportDrawerContent auth={auth} language={language} onUploaded={onUploaded} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function applySseEvent(
  assistantId: string,
  event: SseEvent,
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  onRequestContext: (requestId: string | null, traceId: string | null) => void,
  onSessionId: (sessionId: string) => void
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
      if (event.data.session_id !== undefined && event.data.session_id !== null) {
        onSessionId(event.data.session_id);
      }
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

function historyMessageToChatMessage(message: ChatHistoryMessage): ChatMessage {
  return {
    id: `history-${message.sequence_no}`,
    role: message.role === "assistant" ? "assistant" : "user",
    text: message.content,
    citations: dedupeCitations(message.citations ?? []),
    requestId: message.request_id,
    traceId: message.trace_id,
    isFinal: true,
    status: message.no_answer === true ? "no_answer" : "answered",
    toolEvents: []
  };
}

function chatSessionStorageKey(auth: AuthSession): string {
  return `aegisrag.chat.session.${auth.tenantId ?? "jwt-tenant"}.${auth.userId ?? "jwt-user"}`;
}

function readStoredSessionId(key: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const value = window.sessionStorage.getItem(key)?.trim();
  return value === undefined || value.length === 0 ? null : value;
}

function writeStoredSessionId(key: string, sessionId: string): void {
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem(key, sessionId);
  }
}

function removeStoredSessionId(key: string): void {
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(key);
  }
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

export function groupCitationsForDisplay(citations: Citation[]): CitationDisplayGroup[] {
  const groups = new Map<string, CitationDisplayGroup>();
  for (const citation of citations) {
    const label = citationSourceLabel(citation);
    const key = [
      citation.document_id,
      citation.version_id ?? "",
      citation.source_display_name ?? "",
      citation.source ?? "",
      label
    ].join(":");
    const existing = groups.get(key);
    if (existing !== undefined) {
      existing.count += 1;
      continue;
    }
    groups.set(key, {
      key,
      primary: citation,
      label: citation.version_id !== undefined && citation.version_id !== null ? `${label} · ${citation.version_id}` : label,
      count: 1
    });
  }
  return [...groups.values()];
}

function answerWithCitations(message: ChatMessage): string {
  const citationLines = groupCitationsForDisplay(message.citations).map(
    (group, index) => `[${index + 1}] ${group.label}${group.count > 1 ? ` (${group.count} chunks)` : ""}`
  );
  return [message.text, "", "Citations:", ...citationLines].join("\n");
}
