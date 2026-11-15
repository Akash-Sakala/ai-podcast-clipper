import { createHash } from "node:crypto";

/**
 * A JSON response with an ETag over its body. A request that already has the same
 * version (If-None-Match) gets an empty 304 instead, so polls cost almost nothing
 * when nothing changed. Server only.
 */
export function jsonWithEtag(request: Request, data: unknown) {
  const body = JSON.stringify(data);
  const etag = `W/"${createHash("sha1").update(body).digest("base64url").slice(0, 27)}"`;
  const headers = { ETag: etag, "Cache-Control": "no-cache" };
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
