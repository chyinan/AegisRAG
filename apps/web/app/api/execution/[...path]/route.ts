import { proxyExecution } from "@/lib/server/execution-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyExecution(request, (await context.params).path);
}
