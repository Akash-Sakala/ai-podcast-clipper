import { createHash } from "node:crypto";

/**
 * A JSON response with an ETag over its body. A request that already has the same
 * version (If-None-Match) gets an empty 304 instead, so polls cost almost nothing
 * when nothing changed. `servedAt` (server time in ms, taken before the data was
 * read) goes out as `X-Served-At` on both, so the browser can tell which of two
 * answers is newer (lib/job-sync.ts). Server only.
 */
export function jsonWithEtag(
  request: Request,
  data: unknown,
  servedAt: number = Date.now(),
) {
  const body = JSON.stringify(data);
  const etag = `W/"${createHash("sha1").update(body).digest("base64url").slice(0, 27)}"`;
  const headers = {
    ETag: etag,
    "Cache-Control": "no-cache",
    "X-Served-At": String(servedAt),
  };
  const known = (request.headers.get("if-none-match") ?? "")
    .split(",")
    .map((tag) => tag.trim());
  if (known.includes("*") || known.includes(etag)) {
    return new Response(null, { status: 304, headers });
  }
  return new Response(body, {
    headers: { ...headers, "Content-Type": "application/json" },
  });
}
