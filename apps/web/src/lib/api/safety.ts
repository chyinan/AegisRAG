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
    return "缺少认证上下文。请先选择本地身份或使用企业 JWT 登录。";
  }
  if (errorCode?.includes("FORBIDDEN") === true || errorCode?.includes("DENIED") === true) {
    return "当前身份没有执行该操作的权限。";
  }
  return "请求未完成。系统只展示安全错误摘要，请复制 request_id 进一步排查。";
}
