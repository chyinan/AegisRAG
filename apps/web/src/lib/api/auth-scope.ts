import type { AuthSession } from "@/lib/auth";

// Credentials must never be copied into a query key, storage key or DOM key.
// Auth sessions are immutable in the workbench. A refreshed/replaced bearer
// session intentionally receives a fresh opaque cache scope, including tokens
// whose user/tenant claims are not available to the frontend.
const bearerScopes = new WeakMap<AuthSession, { credential: string | undefined; id: number }>();
let nextBearerScope = 0;

export function authScopeKey(auth: AuthSession): string {
  let bearerScope: number | null = null;
  if (auth.mode === "bearer") {
    let scope = bearerScopes.get(auth);
    if (scope === undefined || scope.credential !== auth.bearerToken) {
      scope = { credential: auth.bearerToken, id: ++nextBearerScope };
      bearerScopes.set(auth, scope);
    }
    bearerScope = scope.id;
  }
  return JSON.stringify([
    auth.mode,
    auth.tenantId ?? null,
    auth.userId ?? null,
    auth.department ?? null,
    [...auth.roles].sort(),
    [...auth.permissions].sort(),
    bearerScope
  ]);
}

export function documentReviewKey(auth: AuthSession) {
  return ["documents-review", authScopeKey(auth)] as const;
}

// A document-wide prefix intentionally covers every version, while retaining
// the exact authorization scope (including a replaced bearer session).
export function documentStatusKey(auth: AuthSession, documentId: string) {
  return ["document-version-status", authScopeKey(auth), documentId] as const;
}
