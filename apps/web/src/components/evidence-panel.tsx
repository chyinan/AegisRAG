"use client";

import { useMutation } from "@tanstack/react-query";
import { SearchCheck } from "lucide-react";
import { useEffect } from "react";
import { resolveSource } from "@/lib/api/client";
import { safeErrorMessage } from "@/lib/api/safety";
import type { Citation, SourceResolveResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import { CopyIdButton, SafeErrorBanner, StatusPill, citationLabel } from "./primitives";

export function EvidencePanel({
  auth,
  citation
}: Readonly<{ auth: AuthSession; citation: Citation | null }>) {
  const mutation = useMutation<SourceResolveResult, Error, Citation>({
    mutationFn: (selected) =>
      resolveSource(auth, {
        document_id: selected.document_id,
        version_id: selected.version_id,
        chunk_id: selected.chunk_id
      })
  });

  useEffect(() => {
    mutation.reset();
    if (citation !== null) {
      mutation.mutate(citation);
    }
  }, [citation?.document_id, citation?.version_id, citation?.chunk_id]);

  if (citation === null) {
    return (
      <div className="evidence-body">
        <StatusPill tone="source">Citation ready</StatusPill>
        <p className="muted">点击回答中的 citation 后，系统会重新调用 /sources/resolve 做二次授权。</p>
      </div>
    );
  }

  if (mutation.isPending) {
    return (
      <div className="evidence-body">
        <div className="actions-row">
          <SearchCheck aria-hidden="true" />
          <strong>正在重新授权来源</strong>
        </div>
        <p className="muted">{citationLabel(citation)}</p>
      </div>
    );
  }

  if (mutation.isError) {
    return <SafeErrorBanner message={safeErrorMessage(undefined)} />;
  }

  const source = mutation.data;
  if (source?.authorized === false) {
    return (
      <SafeErrorBanner
        code="SOURCE_NOT_AVAILABLE"
        message="当前身份无法查看该来源片段。系统不暴露资源是否存在、是否删除或 ACL 是否匹配。"
      />
    );
  }

  return (
    <div className="evidence-body">
      <div className="actions-row">
        <StatusPill tone="source">Authorized excerpt</StatusPill>
        <CopyIdButton value={source?.chunk_id ?? citation.chunk_id} label="Copy chunk_id" />
      </div>
      <h3 className="panel-title">{source?.title ?? citationLabel(citation)}</h3>
      <p className="muted">
        page {source?.page_start ?? citation.page_start ?? "-"} - {source?.page_end ?? citation.page_end ?? "-"}
      </p>
      <p className="wrap">{source?.excerpt ?? "后端未返回 excerpt；不使用 provider 输出补造来源。"}</p>
      <div className="chip-row">
        <span className="id-text">document: {source?.document_id ?? citation.document_id}</span>
        <span className="id-text">version: {source?.version_id ?? citation.version_id ?? "-"}</span>
      </div>
    </div>
  );
}
