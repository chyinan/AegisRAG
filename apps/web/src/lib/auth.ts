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
    description: "RAG 对话和 citation",
    icon: MessageSquareText,
    permission: "retrieval:query"
  },
  {
    key: "knowledge",
    label: "Knowledge Base",
    description: "导入、版本和索引状态",
    icon: FolderOpen
  },
  {
    key: "review",
    label: "Review",
    description: "人工审阅和治理队列",
    icon: FileSearch,
    permission: "review:read"
  },
  {
    key: "diagnostics",
    label: "Diagnostics",
    description: "request_id 检索复盘",
    icon: Gauge,
    permission: "diagnostics:read"
  },
  {
    key: "eval",
    label: "Eval",
    description: "质量回归报告",
    icon: Activity,
    permission: "eval:read",
    sensitive: true
  },
  {
    key: "audit",
    label: "Audit",
    description: "安全审计摘要",
    icon: History,
    permission: "audit:read",
    sensitive: true
  },
  {
    key: "agent",
    label: "Agent Runs",
    description: "受控工具调用审阅",
    icon: Bot,
    permission: "agent:run"
  },
  {
    key: "settings",
    label: "Settings",
    description: "身份和集成边界",
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
    label: "员工",
    userId: "demo-user-employee",
    tenantId: "tenant-demo-alpha",
    roles: ["employee"],
    department: "HR",
    permissions: ["document:read", "retrieval:query"],
    defaultSurface: "ask",
    summary: "提问、查看授权 citation 和 evidence",
    icon: MessageSquareText
  },
  knowledge_manager: {
    mode: "dev_headers",
    label: "知识管理员",
    userId: "demo-user-knowledge-manager",
    tenantId: "tenant-demo-alpha",
    roles: ["knowledge_manager"],
    department: "platform",
    permissions: ["document:read", "document:upload", "document:manage", "retrieval:query"],
    defaultSurface: "knowledge",
    summary: "导入文件、管理版本和索引状态",
    icon: FolderOpen
  },
  ai_engineer: {
    mode: "dev_headers",
    label: "AI 工程师",
    userId: "demo-user-ai-engineer",
    tenantId: "tenant-demo-alpha",
    roles: ["ai_engineer"],
    department: "platform",
    permissions: ["document:read", "retrieval:query", "diagnostics:read", "eval:read"],
    defaultSurface: "diagnostics",
    summary: "复盘 retrieval、eval 和 no-answer",
    icon: Gauge
  },
  auditor: {
    mode: "dev_headers",
    label: "审计员",
    userId: "demo-user-auditor",
    tenantId: "tenant-demo-alpha",
    roles: ["auditor"],
    department: "risk",
    permissions: ["document:read", "review:read", "audit:read"],
    defaultSurface: "audit",
    summary: "查看审计摘要和治理队列",
    icon: ShieldCheck
  },
  platform_admin: {
    mode: "dev_headers",
    label: "平台管理员",
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
    summary: "完整治理入口和平台配置边界",
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
