import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import { loadChatHistory } from "./client";

function success() {
  return new Response(JSON.stringify({
    request_id: "req-history-deadline", error: null, data: { session_id: "session-deadline", messages: [] }
  }));
}

function abortable<T>(signal: AbortSignal, genericAbort = false): Promise<T> {
  return new Promise((_resolve, reject) => {
    const abort = () => {
      const reason: unknown = signal.reason;
      reject(!genericAbort && (reason instanceof Error || reason instanceof DOMException)
        ? reason : new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("chat history cooperative deadline", () => {
  it.each([false, true])("times out a cooperative pending fetch after 15 seconds (generic AbortError: %s)", async (genericAbort) => {
    vi.useFakeTimers();
    let localSignal!: AbortSignal;
    const caller = new AbortController();
    const removeListener = vi.spyOn(caller.signal, "removeEventListener");
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      localSignal = init?.signal as AbortSignal;
      return abortable<Response>(localSignal, genericAbort);
    });
    vi.stubGlobal("fetch", fetchMock);
    const promise = loadChatHistory(PERSONAS.employee, "session-deadline", 50, caller.signal);
    const rejection = expect(promise).rejects.toMatchObject({ name: "TimeoutError", message: "History request timed out." });
    await vi.advanceTimersByTimeAsync(14_999);
    expect(localSignal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await rejection;
    expect(localSignal.reason).toMatchObject({ name: "TimeoutError" });
    expect(caller.signal.aborted).toBe(false);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the deadline active through response-body decoding", async () => {
    vi.useFakeTimers();
    let localSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      localSignal = init?.signal as AbortSignal;
      const response = success();
      vi.spyOn(response, "json").mockImplementation(() => abortable(localSignal, true));
      return Promise.resolve(response);
    }));
    const rejection = expect(loadChatHistory(PERSONAS.employee, "session-deadline")).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
    expect(localSignal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([false, true])("preserves the exact caller cancellation reason (already aborted: %s)", async (alreadyAborted) => {
    vi.useFakeTimers();
    const caller = new AbortController();
    const reason = new DOMException("User left the conversation", "AbortError");
    if (alreadyAborted) caller.abort(reason);
    const removeListener = vi.spyOn(caller.signal, "removeEventListener");
    let localSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      localSignal = init?.signal as AbortSignal;
      return abortable<Response>(localSignal, true);
    }));
    const promise = loadChatHistory(PERSONAS.employee, "session-deadline", 50, caller.signal);
    const rejection = expect(promise).rejects.toBe(reason);
    if (!alreadyAborted) caller.abort(reason);
    await rejection;
    expect(localSignal.aborted).toBe(true);
    expect(localSignal.reason).toBe(reason);
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the success timer and caller listener without aborting a completed read", async () => {
    vi.useFakeTimers();
    const caller = new AbortController();
    const removeListener = vi.spyOn(caller.signal, "removeEventListener");
    let localSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      localSignal = init?.signal as AbortSignal;
      return Promise.resolve(success());
    }));
    await expect(loadChatHistory(PERSONAS.employee, "session-deadline", 50, caller.signal)).resolves.toEqual({ session_id: "session-deadline", messages: [] });
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
    caller.abort();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(localSignal.aborted).toBe(false);
  });

  it("cleans up a failed read and preserves its original network error", async () => {
    vi.useFakeTimers();
    const caller = new AbortController();
    const removeListener = vi.spyOn(caller.signal, "removeEventListener");
    const error = new TypeError("Network interrupted");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(error));
    await expect(loadChatHistory(PERSONAS.employee, "session-deadline", 50, caller.signal)).rejects.toBe(error);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
  });
});
