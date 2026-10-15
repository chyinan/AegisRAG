// pattern: Imperative Shell

import {
  Activity,
  Bot,
  FileSearch,
  FolderOpen,
  Gauge,
  History,
  KeyRound,
  MessageSquareText,
  Settings,
  ShieldCheck
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  buildLoginRequest,
  readApiErrorMessage,
  readLoginResponse,
  readLogoutResponse,
  readRefreshResponse,
  type LoginUserOptions
} from "./auth-contract";

class LoginRequestRejectedError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "LoginRequestRejectedError";
    this.status = status;
  }
}

export type AuthMode = "dev_headers" | "bearer";

export type PersonaKey = "employee" | "knowledge_manager" | "ai_engineer" | "auditor" | "platform_admin";

export type AuthSession = Readonly<{
  mode: AuthMode;
  label: string;
  userId?: string;
  tenantId?: string;
  roles: readonly string[];
  department?: string;
  permissions: readonly string[];
  bearerToken?: string;
  refreshToken?: string;
  /** Conservative access-token deadline, derived from login expires_in; never persisted. */
  expiresAt?: number;
}>;

export type SurfaceKey =
  | "ask"
  | "knowledge"
  | "review"
  | "diagnostics"
  | "eval"
  | "audit"
  | "agent"
  | "settings";

export type NavItem = {
  key: SurfaceKey;
  label: string;
  description: string;
  icon: LucideIcon;
  permission?: string;
  sensitive?: boolean;
};

export const NAV_ITEMS: NavItem[] = [
  {
    key: "ask",
    label: "Ask",
    description: "RAG Q&A",
    icon: MessageSquareText,
    permission: "retrieval:query"
  },
  {
    key: "knowledge",
    label: "Knowledge Base",
    description: "Import, versions, indexing",
    icon: FolderOpen
  },
  {
    key: "review",
    label: "Review",
    description: "Human review queue",
    icon: FileSearch,
    permission: "review:read"
  },
  {
    key: "diagnostics",
    label: "Diagnostics",
    description: "request_id trace review",
    icon: Gauge,
    permission: "diagnostics:read"
  },
  {
    key: "eval",
    label: "Eval",
    description: "Quality regression",
    icon: Activity,
    permission: "eval:read",
    sensitive: true
  },
  {
    key: "audit",
    label: "Audit",
    description: "Security audit",
    icon: History,
    permission: "audit:read",
    sensitive: true
  },
  {
    key: "agent",
    label: "Agent Runs",
    description: "Tool-call review",
    icon: Bot,
    permission: "agent:run"
  },
  {
    key: "settings",
    label: "Settings",
    description: "Identity boundaries",
    icon: Settings,
    permission: "admin:settings",
    sensitive: true
  }
];

export const PERSONAS: Record<
  PersonaKey,
  AuthSession & { defaultSurface: SurfaceKey; summary: string; icon: LucideIcon }
> = {
  employee: {
    mode: "dev_headers",
    label: "Employee",
    userId: "demo-user-employee",
    tenantId: "tenant-demo-alpha",
    roles: ["employee"],
    department: "HR",
    permissions: ["document:read", "retrieval:query"],
    defaultSurface: "ask",
    summary: "Ask and view authorized citations",
    icon: MessageSquareText
  },
  knowledge_manager: {
    mode: "dev_headers",
    label: "Knowledge Manager",
    userId: "demo-user-knowledge-manager",
    tenantId: "tenant-demo-alpha",
    roles: ["knowledge_manager"],
    department: "platform",
    permissions: ["document:read", "document:upload", "document:manage", "retrieval:query"],
    defaultSurface: "knowledge",
    summary: "Import documents and monitor indexing",
    icon: FolderOpen
  },
  ai_engineer: {
    mode: "dev_headers",
    label: "AI Engineer",
    userId: "demo-user-ai-engineer",
    tenantId: "tenant-demo-alpha",
    roles: ["ai_engineer"],
    department: "platform",
    permissions: ["document:read", "retrieval:query", "diagnostics:read", "eval:read"],
    defaultSurface: "diagnostics",
    summary: "Review retrieval, eval, and no-answer",
    icon: Gauge
  },
  auditor: {
    mode: "dev_headers",
    label: "Auditor",
    userId: "demo-user-auditor",
    tenantId: "tenant-demo-alpha",
    roles: ["auditor"],
    department: "risk",
    permissions: ["document:read", "review:read", "audit:read"],
    defaultSurface: "audit",
    summary: "Review audit and governance queues",
    icon: ShieldCheck
  },
  platform_admin: {
    mode: "dev_headers",
    label: "Platform Admin",
    userId: "demo-user-platform-admin",
    tenantId: "tenant-demo-alpha",
    roles: ["admin", "platform_admin"],
    department: "platform",
    permissions: [
      "document:read",
      "document:upload",
      "document:manage",
      "retrieval:query",
      "diagnostics:read",
      "eval:read",
      "audit:read",
      "audit:export",
      "review:read",
      "agent:run",
      "admin:settings"
    ],
    defaultSurface: "knowledge",
    summary: "Full governance and platform boundaries",
    icon: KeyRound
  }
};

export function hasPermission(auth: AuthSession, permission: string | undefined): boolean {
  if (permission === undefined) {
    return true;
  }
  return auth.permissions.includes(permission);
}

export function defaultSurfaceFor(auth: AuthSession): SurfaceKey {
  const knownPersona = Object.values(PERSONAS).find((persona) => persona.userId === auth.userId);
  return knownPersona?.defaultSurface ?? "ask";
}

export function authHeaders(auth: AuthSession): HeadersInit {
  if (auth.mode === "bearer" && auth.bearerToken !== undefined) {
    return {
      Authorization: `Bearer ${auth.bearerToken}`
    };
  }

  return {
    "X-User-ID": auth.userId ?? "",
    "X-Tenant-ID": auth.tenantId ?? "",
    "X-Roles": auth.roles.join(","),
    "X-Department": auth.department ?? "",
    "X-Permissions": auth.permissions.join(",")
  };
}

export type LoginError = {
  code: string;
  message: string;
};

export async function loginUser(options: LoginUserOptions): Promise<AuthSession> {
  const TIMEOUT_MS = 30000; // 30s
  const MAX_RETRIES = 3;

  let lastError: Error | null = null;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const requestStartedAt = Date.now();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildLoginRequest(options)),
        signal: controller.signal,
      });

      if (!response.ok) {
        let message = "Login failed";
        try {
          const errorBody: unknown = await response.json();
          message = readApiErrorMessage(errorBody) ?? message;
        } catch {
          // use default message
        }
        if (response.status >= 400 && response.status < 500) {
          throw new LoginRequestRejectedError(message, response.status);
        }
        throw new Error(message);
      }

      const apiResponse: unknown = await response.json();
      const data = readLoginResponse(apiResponse);
      if (data === null) throw new LoginRequestRejectedError("Login response is invalid", response.status);
      const expiresAt = requestStartedAt + data.expires_in * 1000;
      if (!Number.isFinite(expiresAt) || expiresAt <= requestStartedAt || expiresAt > Number.MAX_SAFE_INTEGER) {
        throw new LoginRequestRejectedError("Login response expiry is invalid", response.status);
      }

      return Object.freeze({
        mode: "bearer",
        label: data.display_name ?? data.user_id,
        userId: data.user_id,
        tenantId: data.tenant_id,
        roles: Object.freeze([...data.roles]),
        department: undefined,
        permissions: Object.freeze([...data.permissions]),
        bearerToken: data.access_token,
        refreshToken: data.refresh_token,
        expiresAt,
      });
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      // Do not retry on client errors (4xx) or aborted requests
      if (err instanceof DOMException && err.name === "AbortError") {
        lastError = new Error("Login request timed out");
      } else if (err instanceof LoginRequestRejectedError) {
        throw err;
      }
    } finally {
      clearTimeout(timeoutId);
    }

    // Exponential backoff before retry
    if (attempt < MAX_RETRIES - 1) {
      await new Promise((resolve) => setTimeout(resolve, 2 ** attempt * 1000));
    }
  }

  throw lastError ?? new Error("Login failed after retries");
}

export type RefreshResult = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
};

export async function refreshAuth(refreshToken: string): Promise<RefreshResult> {
  const response = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!response.ok) {
    let message = "Token refresh failed";
    try {
      const errorBody: unknown = await response.json();
      message = readApiErrorMessage(errorBody) ?? message;
    } catch {
      // use default message
    }
    throw new Error(message);
  }

  const apiResponse: unknown = await response.json();
  const data = readRefreshResponse(apiResponse);
  if (data === null) throw new Error("Token refresh response is invalid");

  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresIn: data.expires_in,
  };
}


export type LogoutResult = "confirmed" | "unconfirmed";
export const LOGOUT_TIMEOUT_MS = 10_000;

/** One attempt only; the deadline includes response-body parsing, even if a transport stalls. */
export async function logoutUser(auth: AuthSession, signal?: AbortSignal): Promise<LogoutResult> {
  if (auth.mode !== "bearer" || !auth.bearerToken || signal?.aborted) return "unconfirmed";
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  let rejectCancellation: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    rejectCancellation = () => reject(new DOMException("Logout request ended.", "AbortError"));
    controller.signal.addEventListener("abort", rejectCancellation, { once: true });
  });
  const timeout = setTimeout(abort, LOGOUT_TIMEOUT_MS);
  const request = async (): Promise<LogoutResult> => {
    const response = await fetch("/api/auth/logout", {
      method: "POST", headers: { Authorization: `Bearer ${auth.bearerToken}` }, signal: controller.signal
    });
    if (!response.ok) return "unconfirmed";
    const body: unknown = await response.json();
    return readLogoutResponse(body) ? "confirmed" : "unconfirmed";
  };
  try {
    return await Promise.race([request(), cancellation]);
  } catch {
    // Never display network/provider details or confuse local logout with confirmed revocation.
    return "unconfirmed";
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
    if (rejectCancellation) controller.signal.removeEventListener("abort", rejectCancellation);
    controller.abort();
  }
}
