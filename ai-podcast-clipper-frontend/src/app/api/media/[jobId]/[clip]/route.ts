import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { isValidClipName, isValidJobId, jobDir } from "~/lib/jobs";

export const dynamic = "force-dynamic";

const POSTER_PATTERN = /^clip_\d+\.jpg$/;

/**
 * Streams a clip (clip_N.mp4, with HTTP Range support so the player can seek) or
 * its poster still (clip_N.jpg). The app adds ?v=<version> to the URL; a versioned
 * URL never changes content, so the browser may keep it and skip the network.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ jobId: string; clip: string }> },
) {
  const { jobId, clip } = await params;
  const isPoster = POSTER_PATTERN.test(clip);
  if (!isValidJobId(jobId) || !(isPoster || isValidClipName(clip))) {
    return new Response("Not found", { status: 404 });
  }

  const file = path.join(jobDir(jobId), "clips", clip);
  let size: number;
  let modified: Date;
  try {
    const stat = await fsp.stat(file);
    size = stat.size;
    modified = stat.mtime;
  } catch {
    return new Response("Not found", { status: 404 });
  }

  const url = new URL(request.url);
  const download = url.searchParams.has("download");
  const etag = `"${size.toString(36)}-${Math.floor(modified.getTime()).toString(36)}"`;
  const headers: Record<string, string> = {
    "Content-Type": isPoster ? "image/jpeg" : "video/mp4",
    "Accept-Ranges": "bytes",
    ETag: etag,
    "Last-Modified": modified.toUTCString(),
    "Cache-Control": download
      ? "no-store"
      : url.searchParams.has("v")
        ? "private, max-age=31536000, immutable"
        : "private, no-cache",
  };
  if (!download && request.headers.get("if-none-match") === etag) {
    return new Response(null, { status: 304, headers });
  }
  if (download) {
    headers["Content-Disposition"] = `attachment; filename="${jobId}_${clip}"`;
  }

  const range = request.headers.get("range");
  const match = range ? /bytes=(\d*)-(\d*)/.exec(range) : null;

  if (match && size > 0) {
    let start = match[1] ? parseInt(match[1], 10) : 0;
    let end = match[2] ? parseInt(match[2], 10) : size - 1;
    if (!match[1] && match[2]) {
      // suffix range: last N bytes
      start = Math.max(size - parseInt(match[2], 10), 0);
      end = size - 1;
    }
    end = Math.min(end, size - 1);

    if (start > end || start >= size) {
      return new Response(null, {
        status: 416,
        headers: { "Content-Range": `bytes */${size}` },
      });
    }

    const stream = Readable.toWeb(
      fs.createReadStream(file, { start, end }),
    ) as ReadableStream;
    return new Response(stream, {
      status: 206,
      headers: {
        ...headers,
        "Content-Range": `bytes ${start}-${end}/${size}`,
        "Content-Length": String(end - start + 1),
      },
    });
  }

  const stream = Readable.toWeb(fs.createReadStream(file)) as ReadableStream;
  return new Response(stream, {
    status: 200,
    headers: { ...headers, "Content-Length": String(size) },
  });
}
