"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClientError } from "@/lib/api/client";
export const OPERATOR_REQUEST_TIMEOUT_MS = 30_000;
export type OperatorError = {
  code: string;
  requestId?: string;
};

/** One in-flight operation per mounted authorization scope, including body reads. */
export function useOperatorRequest() {
  const pending = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<OperatorError | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      pending.current?.abort();
      pending.current = null;
    };
  }, []);
  const execute = useCallback(async <T,>(task: (signal: AbortSignal) => Promise<T>, success: (value: T) => void, timeoutMs = OPERATOR_REQUEST_TIMEOUT_MS) => {
    if (!mounted.current || pending.current !== null) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    let cancel: (() => void) | undefined;
    const cancellation = new Promise<never>((_resolve, reject) => {
      cancel = () => reject(new DOMException("Operation ended", "AbortError"));
      controller.signal.addEventListener("abort", cancel, {
        once: true
      });
    });
    const timeout = setTimeout(() => controller.abort(), Math.min(315_000, Math.max(1, timeoutMs)));
    const current = () => mounted.current && pending.current === controller;
    try {
      const value = await Promise.race([task(controller.signal), cancellation]);
      if (current() && !controller.signal.aborted) success(value);
    } catch (failure) {
      if (current()) setError(failure instanceof ApiClientError ? {
        code: failure.structured.code,
        requestId: failure.structured.request_id ?? undefined
      } : {
        code: "OPERATION_UNCONFIRMED"
      });
    } finally {
      clearTimeout(timeout);
      if (cancel) controller.signal.removeEventListener("abort", cancel);
      if (current()) {
        pending.current = null;
        setBusy(false);
      }
      controller.abort();
    }
  }, []);
  return {
    busy,
    error,
    execute
  };
}
