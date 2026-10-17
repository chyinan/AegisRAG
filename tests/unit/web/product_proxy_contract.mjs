import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import config from "../../../apps/web/next.config.mjs";
import { proxyExecution, EXECUTION_PROXY_BUDGET_MS } from "../../../apps/web/src/lib/server/execution-proxy.ts";

assert.equal(config.experimental?.proxyTimeout, undefined);
const rewrites = await config.rewrites();
assert.ok(rewrites.some((route) => route.source === "/api/backend/:path*"));
assert.ok(!rewrites.some((route) => route.source.includes("execution")));
assert.deepEqual(EXECUTION_PROXY_BUDGET_MS, {
  requestBody: 10_000, stateRead: 10_000, background: 30_000,
  agent: 135_000, folder: 315_000, maximumInline: 3_615_000
});
assert.ok(Object.values(EXECUTION_PROXY_BUDGET_MS).every((value) => Number.isFinite(value) && value > 0));
const route = await readFile(new URL("../../../apps/web/app/api/execution/[...path]/route.ts", import.meta.url), "utf8");
assert.match(route, /runtime = "nodejs"/);
assert.match(route, /proxyExecution\(request/);
let forwarded = false;
const response = await proxyExecution(new Request("http://workbench.test/api/execution/agent/run", {
  method: "POST", headers: { "content-type": "application/json", "x-request-id": "contract-request" }, body: '{"input":"local fixture"}'
}), ["agent", "run"], {
  apiBaseUrl: "http://fixture.test:8000",
  fetch: async (url, init) => {
    assert.equal(String(url), "http://fixture.test:8000/agent/run");
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "manual");
    assert.equal(new Headers(init.headers).get("x-request-id"), "contract-request");
    forwarded = true;
    return new Response('{"request_id":"contract-request","data":null,"error":{"code":"FIXTURE"}}', { status: 409 });
  }
});
assert.ok(forwarded);
assert.equal(response.status, 409);
assert.equal((await response.json()).error.code, "FIXTURE");
console.log("Product proxy contract passed: scoped route, finite budgets, unchanged ordinary rewrites, preserved response");
