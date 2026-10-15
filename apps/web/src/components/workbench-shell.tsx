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
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiClientError, loadChatHistory, streamChat } from "@/lib/api/client";
import { authScopeKey } from "@/lib/api/auth-scope";
import { SseProtocolError } from "@/lib/api/sse";
import { applyChatEvent, canCopyAnswer, failChatMessage, historyMessageToChatMessage, type ChatMessage } from "@/lib/chat-stream-state";
import { answerWithCitations } from "@/lib/citation-markers";
import type { Citation, UploadDocumentResult } from "@/lib/api/types";
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
import { UploadStatusCard } from "./upload-status";

export type CitationDisplayGroup = {
  key: string;
  primary: Citation;
  label: string;
  count: number;
};

type WorkbenchProps = Readonly<{
  auth: AuthSession;
  language: Language;
  onLanguageChange: (language: Language) => void;
  onSignOut: () => void;
}>;

export function WorkbenchShell(props: WorkbenchProps) {
  return <WorkbenchContent key={authScopeKey(props.auth)} {...props} />;
}

function WorkbenchContent({ auth, language, onLanguageChange, onSignOut }: WorkbenchProps) {
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
          <UploadStatusCard auth={auth} result={quickImportResult} language={language} />
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
              if (requestId === null && traceId === null) setSelectedCitation(null);
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
  const [historyError, setHistoryError] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const operationRef = useRef<{ controller: AbortController; kind: "history" | "stream" } | null>(null);
  // Identity changes remount this workbench. Language changes must never reload history.
  const initialAuth = useRef(auth).current;
  const requestContextRef = useRef(onRequestContext);
  requestContextRef.current = onRequestContext;
  const canAsk = hasPermission(auth, "retrieval:query");
  const storageKey = chatSessionStorageKey(auth);

  useEffect(() => {
    const storedSessionId = canAsk ? readStoredSessionId(storageKey) : null;
    if (storedSessionId !== null) {
      const controller = new AbortController();
      operationRef.current = { controller, kind: "history" };
      setIsRestoring(true);
      setSessionId(storedSessionId);
      void loadChatHistory(initialAuth, storedSessionId, 50, controller.signal)
        .then((history) => {
          if (operationRef.current?.controller !== controller || controller.signal.aborted) return;
          if (history.session_id !== storedSessionId) throw new Error("History session mismatch");
          const restored = history.messages
            .filter((message) => message.role === "user" || message.role === "assistant")
            .map(historyMessageToChatMessage);
          setMessages(restored);
          setHistoryRestored(restored.length > 0);
          setHistoryError(false);
          const latest = restored.at(-1);
          requestContextRef.current(latest?.requestId ?? null, latest?.traceId ?? null);
        })
        .catch(() => {
          if (operationRef.current?.controller !== controller || controller.signal.aborted) return;
          removeStoredSessionId(storageKey);
          setSessionId(null);
          setHistoryRestored(false);
          setHistoryError(true);
        })
        .finally(() => {
          if (operationRef.current?.controller !== controller) return;
          operationRef.current = null;
          setIsRestoring(false);
        });
    }
    return () => {
      operationRef.current?.controller.abort();
      operationRef.current = null;
    };
  }, [initialAuth, canAsk, storageKey]);

  async function submit() {
    // Ref guard closes the same-render/double-click window before React disables the button.
    if (!canAsk || question.trim().length === 0 || operationRef.current !== null) return;
    const controller = new AbortController();
    operationRef.current = { controller, kind: "stream" };
    const userText = question.trim();
    setQuestion("");
    const initialAssistant: ChatMessage = {
      id: crypto.randomUUID(), role: "assistant", text: "", citations: [], isFinal: false, toolEvents: []
    };
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(), role: "user", text: userText, citations: [], isFinal: true, toolEvents: []
    };
    let assistant = initialAssistant;
    const assistantId = assistant.id;
    setMessages((current) => [...current, userMessage, initialAssistant]);
    setIsStreaming(true);
    const stillCurrent = () => operationRef.current?.controller === controller && !controller.signal.aborted;
    const updateAssistant = (next: ChatMessage) => {
      setMessages((current) => current.map((message) => message.id === assistantId ? next : message));
    };

    try {
      for await (const event of streamChat(auth, userText, sessionId, controller.signal)) {
        if (!stillCurrent()) return;
        assistant = applyChatEvent(assistant, event);
        updateAssistant(assistant);
        onRequestContext(event.data.request_id, event.data.trace_id);
        if (event.type === "final" && event.data.session_id) {
          setSessionId(event.data.session_id);
          writeStoredSessionId(storageKey, event.data.session_id);
        }
      }
      // Also enforce completion here, so a replaced transport cannot silently leave a pending bubble.
      if (stillCurrent() && !assistant.isFinal) throw new SseProtocolError("CHAT_STREAM_INCOMPLETE");
    } catch (streamError) {
      if (!stillCurrent()) return;
      const error = streamError instanceof ApiClientError || streamError instanceof SseProtocolError
        ? streamError.structured
        : { code: "CHAT_STREAM_FAILED", message: "The connection was interrupted. Please try again." };
      assistant = failChatMessage(assistant, error);
      updateAssistant(assistant);
      onRequestContext(assistant.requestId ?? null, assistant.traceId ?? null);
    } finally {
      if (stillCurrent()) {
        operationRef.current = null;
        setIsStreaming(false);
        if (assistant.status === "error") setQuestion((current) => current || userText);
      }
    }
  }

  function startNewConversation() {
    operationRef.current?.controller.abort();
    operationRef.current = null;
    setIsStreaming(false);
    setIsRestoring(false);
    setQuestion("");
    setMessages([]);
    setSessionId(null);
    setHistoryRestored(false);
    setHistoryError(false);
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
          {isRestoring && <span role="status" className="muted">{language === "zh" ? "正在恢复对话…" : "Restoring conversation…"}</span>}
          {historyError && <span className="muted">{text(uiText.historyUnavailable, language)}</span>}
        </div>
        <Button type="button" variant="secondary" onClick={startNewConversation}>
          {text(uiText.newConversation, language)}
        </Button>
      </CardInset>

      {!canAsk && <PermissionNotice permission="retrieval:query" language={language} />}

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
            {message.error !== undefined && (
              <SafeErrorBanner code={message.error.code} message={message.error.message} requestId={message.requestId} />
            )}
            {message.role === "assistant" ? (
              <AnswerMarkdown
                answer={message.text || (message.status === "error"
                  ? (language === "zh" ? "回答未完成，请重试。" : "Answer incomplete. Please try again.")
                  : message.isFinal ? text(uiText.cannotConfirm, language) : (language === "zh" ? "正在生成回答…" : "Streaming tokens…"))}
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
                  value={canCopyAnswer(message) ? answerWithCitations(message) : ""}
                  label={text(uiText.copyAnswerWithCitations, language)}
                  disabled={!canCopyAnswer(message)}
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
            aria-label={language === "zh" ? "你的问题" : "Your question"}
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
              disabled={!canAsk || question.trim().length === 0 || isStreaming || isRestoring}
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

function chatSessionStorageKey(auth: AuthSession): string | null {
  // Never restore a different bearer user's conversation through a shared fallback key.
  if (!auth.tenantId || !auth.userId) return null;
  return `aegisrag.chat.session.${auth.tenantId}.${auth.userId}`;
}

function readStoredSessionId(key: string | null): string | null {
  if (typeof window === "undefined" || key === null) return null;
  try {
    return window.sessionStorage.getItem(key)?.trim() || null;
  } catch {
    // Storage can be disabled by a privacy policy. In-memory chat remains usable.
    return null;
  }
}

function writeStoredSessionId(key: string | null, sessionId: string): void {
  if (typeof window === "undefined" || key === null) return;
  try {
    window.sessionStorage.setItem(key, sessionId);
  } catch {
    // A successful answer must not become a failed answer because persistence is unavailable.
  }
}

function removeStoredSessionId(key: string | null): void {
  if (typeof window === "undefined" || key === null) return;
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Best-effort only; the current in-memory session has already been reset.
  }
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

