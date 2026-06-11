import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const runtime = "nodejs";

const ALLOWED_ASSETS = new Map([
  ["sidecar.css", "text/css; charset=utf-8"],
  ["sidecar.js", "application/javascript; charset=utf-8"]
]);

export async function GET(_request: Request, context: { params: Promise<{ assetPath: string[] }> }) {
  const { assetPath } = await context.params;
  const assetName = assetPath.join("/");
  const contentType = ALLOWED_ASSETS.get(assetName);

  if (contentType === undefined) {
    return new Response("Not found", { status: 404 });
  }

  const asset = await readFile(join(process.cwd(), "sidecar", assetName), "utf8");
  return new Response(asset, {
    headers: {
      "content-type": contentType
    }
  });
}
