import { AgentProtocolError } from "./agent-contract";

/** UUID4 is a private owner-scoped recovery reference, never authorization. */
export function isDurableStartupKey(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function durableStartupKey(value: unknown): string {
  if (!isDurableStartupKey(value)) throw new AgentProtocolError("AGENT_INVALID_STARTUP_KEY");
  return value.toLowerCase();
}

export function createDurableStartupKey(): string {
  try {
    const crypto = globalThis.crypto;
    if (typeof crypto?.randomUUID === "function") return durableStartupKey(crypto.randomUUID());
    if (typeof crypto?.getRandomValues === "function") {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
      return durableStartupKey(`${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`);
    }
  } catch {
    // Never expose entropy-provider failures, rejected values, or use Math.random.
  }
  throw new AgentProtocolError("AGENT_SECURE_RANDOM_UNAVAILABLE");
}
