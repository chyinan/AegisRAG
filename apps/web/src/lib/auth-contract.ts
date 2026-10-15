// pattern: Functional Core

export type LoginUserOptions = Readonly<{
  tenantId: string;
  username: string;
  password: string;
}>;

export type LoginRequest = Readonly<{
  tenant_id: string;
  username: string;
  password: string;
}>;

export type LoginResponseData = Readonly<{
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
  display_name: string;
  tenant_id: string;
  roles: Array<string>;
  permissions: Array<string>;
}>;

export type RefreshResponseData = Readonly<{
  access_token: string;
  refresh_token: string;
  expires_in: number;
}>;

export function buildLoginRequest(options: LoginUserOptions): LoginRequest {
  return {
    tenant_id: options.tenantId,
    username: options.username,
    password: options.password
  };
}

export function readApiErrorMessage(value: unknown): string | null {
  if (!isRecord(value) || !isRecord(value.error)) return null;
  return typeof value.error.message === "string" ? value.error.message : null;
}

export function readLoginResponse(value: unknown): LoginResponseData | null {
  if (!isRecord(value) || !isRecord(value.data)) return null;
  const data = value.data;
  return isLoginResponseData(data) ? data : null;
}

export function readRefreshResponse(value: unknown): RefreshResponseData | null {
  if (!isRecord(value) || !isRecord(value.data)) return null;
  const data = value.data;
  return isRefreshResponseData(data) ? data : null;
}

/** Confirm revocation only from the backend's explicit successful envelope. */
export function readLogoutResponse(value: unknown): boolean {
  return isRecord(value) && value.error === null &&
    typeof value.request_id === "string" && value.request_id.trim().length > 0 &&
    isRecord(value.data) && value.data.revoked === true;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isLoginResponseData(value: unknown): value is LoginResponseData {
  return (
    isRecord(value) &&
    typeof value.access_token === "string" &&
    typeof value.refresh_token === "string" &&
    typeof value.token_type === "string" &&
    typeof value.expires_in === "number" &&
    Number.isFinite(value.expires_in) &&
    value.expires_in > 0 &&
    typeof value.user_id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.tenant_id === "string" &&
    isStringArray(value.roles) &&
    isStringArray(value.permissions)
  );
}

function isRefreshResponseData(value: unknown): value is RefreshResponseData {
  return (
    isRecord(value) &&
    typeof value.access_token === "string" &&
    typeof value.refresh_token === "string" &&
    typeof value.expires_in === "number" &&
    Number.isFinite(value.expires_in) &&
    value.expires_in > 0
  );
}

function isStringArray(value: unknown): value is Array<string> {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
