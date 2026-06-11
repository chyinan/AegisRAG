export const FORBIDDEN_RESPONSE_FIELDS = new Set([
  "source_uri",
  "object_key",
  "acl",
  "prompt",
  "chunk_content",
  "provider_raw_response",
  "raw_arguments",
  "raw_output",
  "access_token",
  "secret"
]);

export function stripForbiddenFields(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => stripForbiddenFields(item));
  }

  if (value !== null && typeof value === "object") {
    const cleaned: Record<string, unknown> = {};
    for (const [key, nestedValue] of Object.entries(value)) {
      if (FORBIDDEN_RESPONSE_FIELDS.has(key)) {
        continue;
      }
      cleaned[key] = stripForbiddenFields(nestedValue);
    }
    return cleaned;
  }

  return value;
}

export function safeErrorMessage(errorCode: string | undefined): string {
  if (errorCode === "AUTH_CONTEXT_REQUIRED") {
    return "Authentication context is missing. Select a local identity or sign in with an enterprise JWT.";
  }
  if (errorCode?.includes("FORBIDDEN") === true || errorCode?.includes("DENIED") === true) {
    return "This identity does not have permission to perform this action.";
  }
  return "The request did not complete. Only a safe error summary is shown; copy request_id for follow-up.";
}
