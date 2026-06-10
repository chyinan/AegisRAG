import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockResolvedValue(undefined)
  }
});
