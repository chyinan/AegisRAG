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

export type AuthMode = "dev_headers" | "bearer";

export type PersonaKey = "employee" | "knowledge_manager" | "ai_engineer" | "auditor" | "platform_admin";

export type AuthSession = {
  mode: AuthMode;
  label: string;
  userId?: string;
  tenantId?: string;
  roles: string[];
  department?: string;
  permissions: string[];
  bearerToken?: string;
  refreshToken?: string;
};

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

export type LoginRequest = {
  username: string;
  password: string;
};

export type LoginError = {
  code: string;
  message: string;
};

export type ApiErrorResponse = {
  error?: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
};

export async function loginUser(username: string, password: string): Promise<AuthSession> {
  const TIMEOUT_MS = 30000; // 30s
  const MAX_RETRIES = 3;

  let lastError: Error | undefined;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password } satisfies LoginRequest),
        signal: controller.signal,
      });

      if (!response.ok) {
        let message = "Login failed";
        try {
          const errorBody: ApiErrorResponse = await response.json();
          message = errorBody.error?.message || message;
        } catch {
          // use default message
        }
        throw new Error(message);
      }

      const apiResponse: {
        data: {
          access_token: string;
          refresh_token: string;
          token_type: string;
          expires_in: number;
          user_id: string;
          display_name: string;
          tenant_id: string;
          roles: string[];
          permissions: string[];
        };
      } = await response.json();

      const data = apiResponse.data;

      return {
        mode: "bearer",
        label: data.display_name ?? data.user_id,
        userId: data.user_id,
        tenantId: data.tenant_id,
        roles: data.roles,
        department: undefined,
        permissions: data.permissions,
        bearerToken: data.access_token,
        refreshToken: data.refresh_token,
      };
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      // Do not retry on client errors (4xx) or aborted requests
      if (err instanceof DOMException && err.name === "AbortError") {
        lastError = new Error("Login request timed out");
      } else if (err instanceof Error && err.message.includes("Login failed")) {
        // Server returned an error — do not retry
        throw err;
      }
    } finally {
      clearTimeout(timeoutId);
    }

    // Exponential backoff before retry
    if (attempt < MAX_RETRIES - 1) {
      await new Promise((resolve) => setTimeout(resolve, Math.pow(2, attempt) * 1000));
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
      const errorBody: ApiErrorResponse = await response.json();
      message = errorBody.error?.message || message;
    } catch {
      // use default message
    }
    throw new Error(message);
  }

  const apiResponse: {
    data: {
      access_token: string;
      refresh_token: string;
      expires_in: number;
    };
  } = await response.json();

  return {
    accessToken: apiResponse.data.access_token,
    refreshToken: apiResponse.data.refresh_token,
    expiresIn: apiResponse.data.expires_in,
  };
}
