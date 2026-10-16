import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OPERATOR_REQUEST_TIMEOUT_MS, useOperatorRequest } from "./use-operator-request";
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("keeps a normal long folder batch pending past the short read deadline", async () => {
  vi.useFakeTimers();
  const {
    result
  } = renderHook(() => useOperatorRequest());
  let resolve!: (value: string) => void;
  const success = vi.fn();
  let signal!: AbortSignal;
  act(() => {
    void result.current.execute(nextSignal => {
      signal = nextSignal;
      return new Promise<string>(done => {
        resolve = done;
      });
    }, success, 315_000);
  });
  await act(() => vi.advanceTimersByTimeAsync(OPERATOR_REQUEST_TIMEOUT_MS + 1));
  expect(result.current.busy).toBe(true);
  expect(signal.aborted).toBe(false);
  await act(async () => {
    resolve("completed");
    await Promise.resolve();
  });
  expect(success).toHaveBeenCalledWith("completed");
  expect(result.current.busy).toBe(false);
});
it("bounds stalled reads including body parsing and clears the pending guard", async () => {
  vi.useFakeTimers();
  const {
    result
  } = renderHook(() => useOperatorRequest());
  const success = vi.fn();
  let signal!: AbortSignal;
  act(() => {
    void result.current.execute(nextSignal => {
      signal = nextSignal;
      return new Promise<never>(() => {});
    }, success);
  });
  await act(() => vi.advanceTimersByTimeAsync(OPERATOR_REQUEST_TIMEOUT_MS));
  expect(signal.aborted).toBe(true);
  expect(success).not.toHaveBeenCalled();
  expect(result.current.busy).toBe(false);
  expect(result.current.error?.code).toBe("OPERATION_UNCONFIRMED");
  await act(async () => {
    await result.current.execute(() => Promise.resolve("refreshed"), success);
  });
  expect(success).toHaveBeenCalledWith("refreshed");
});
it("ignores late completion after unmount and blocks same-render repeat operations", async () => {
  const {
    result,
    unmount
  } = renderHook(() => useOperatorRequest());
  let resolve!: (value: string) => void;
  let signal!: AbortSignal;
  const task = vi.fn((nextSignal: AbortSignal) => {
    signal = nextSignal;
    return new Promise<string>(done => {
      resolve = done;
    });
  });
  const success = vi.fn();
  act(() => {
    void result.current.execute(task, success);
    void result.current.execute(task, success);
  });
  expect(task).toHaveBeenCalledTimes(1);
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    resolve("old result");
    await Promise.resolve();
  });
  expect(success).not.toHaveBeenCalled();
});
