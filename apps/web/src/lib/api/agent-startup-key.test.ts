import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentProtocolError } from "./agent-contract";
import { createDurableStartupKey, durableStartupKey, isDurableStartupKey } from "./agent-startup-key";

const key = "587629a6-65a2-4db2-8e18-c17d8190c0ac";
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("private durable startup keys", () => {
  it("uses secure randomUUID and validates its UUID4 result", () => {
    const randomUUID = vi.fn().mockReturnValue(key.toUpperCase()); vi.stubGlobal("crypto", { randomUUID });
    expect(createDurableStartupKey()).toBe(key);
    expect(randomUUID).toHaveBeenCalledOnce();
  });
  it("uses secure getRandomValues when randomUUID is unavailable and sets RFC version/variant bits", () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => { bytes.fill(255); return bytes; }); vi.stubGlobal("crypto", { getRandomValues });
    expect(createDurableStartupKey()).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
    expect(getRandomValues).toHaveBeenCalledOnce();
  });
  it.each([undefined, {}, { randomUUID: () => "invalid" }, { randomUUID: () => { throw new Error("private failure"); } }])("fails closed without usable secure generation", (crypto) => {
    vi.stubGlobal("crypto", crypto);
    const random = vi.spyOn(Math, "random");
    expect(() => createDurableStartupKey()).toThrowError(AgentProtocolError);
    expect(random).not.toHaveBeenCalled();
  });
  it("normalizes UUID4 case without accepting other UUID versions, paths, or surrounding whitespace", () => {
    expect(durableStartupKey(key.toUpperCase())).toBe(key);
    for (const invalid of [null, 42, {}, key.replace("4db2", "5db2"), key.replace("8e18", "7e18"), ` ${key}`, `${key}/run`]) {
      expect(isDurableStartupKey(invalid)).toBe(false);
      expect(() => durableStartupKey(invalid)).toThrow();
    }
  });
});
