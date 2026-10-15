"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { authScopeKey, documentReviewKey } from "@/lib/api/auth-scope";
import { ApiClientError, getDocumentVersionStatus } from "@/lib/api/client";
import type { DocumentVersionStatusResult, UploadDocumentResult } from "@/lib/api/types";
import { hasPermission, type AuthSession } from "@/lib/auth";

const ACTIVE_STATUSES = new Set([
  "queued", "uploaded", "processing", "parsing", "parsed", "chunking", "chunked", "embedding", "indexing", "failed_retryable"
]);

export function documentStatusPollDelay(status: DocumentVersionStatusResult | undefined, now = Date.now()): number | false {
  if (status === undefined || !ACTIVE_STATUSES.has(status.status) || status.deleted_at != null) {
    return false;
  }
  if (status.status === "failed_retryable" && status.next_retry_at != null) {
    const retryAt = Date.parse(status.next_retry_at);
    if (Number.isFinite(retryAt)) {
      return Math.min(30_000, Math.max(2_000, retryAt - now));
    }
  }
  return 2_000;
}

export function shouldRetryStatusFetch(failureCount: number, error: Error): boolean {
  if (failureCount >= 3 || error.name === "AbortError") {
    return false;
  }
  if (error instanceof ApiClientError) {
    return error.status === 408 || error.status === 429 || (error.status !== undefined && error.status >= 500);
  }
  return error instanceof TypeError || error.name === "TimeoutError";
}

export function useDocumentStatus(auth: AuthSession, result: UploadDocumentResult) {
  const queryClient = useQueryClient();
  const scope = authScopeKey(auth);
  const canReadStatus = hasPermission(auth, "document:manage");
  const query = useQuery({
    queryKey: ["document-version-status", scope, result.document_id, result.version_id],
    queryFn: ({ signal }) => getDocumentVersionStatus(auth, result.document_id, result.version_id, signal),
    enabled: canReadStatus,
    retry: shouldRetryStatusFetch,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
    refetchInterval: (query) => query.state.status === "error" ? false : documentStatusPollDelay(query.state.data),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retryOnMount: false,
    refetchOnMount: (query) => query.state.data === undefined || documentStatusPollDelay(query.state.data) !== false
  });

  // Update the list when the backend confirms a stage transition, including
  // completion/failure. Refreshes stay inside the original authorization scope.
  const status = query.data?.status;
  useEffect(() => {
    if (status !== undefined) {
      void queryClient.invalidateQueries({ queryKey: documentReviewKey(auth), exact: true });
    }
  }, [auth, queryClient, scope, status]);

  return { ...query, canReadStatus };
}
