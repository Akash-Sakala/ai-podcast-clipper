import fs from "node:fs";
import fsp from "node:fs/promises";
import { Readable } from "node:stream";
import { clipPath, isValidClipName, isValidJobId } from "~/lib/jobs";

export const dynamic = "force-dynamic";

/** Streams a clip from disk, with HTTP Range support so the video player can seek. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ jobId: string; clip: string }> },
) {
  const { jobId, clip } = await params;
  if (!isValidJobId(jobId) || !isValidClipName(clip)) {
    return new Response("Not found", { status: 404 });
  }

  const file = clipPath(jobId, clip);
  let size: number;
  try {
    size = (await fsp.stat(file)).size;
  } catch {
    return new Response("Not found", { status: 404 });
  }

  const download = new URL(request.url).searchParams.has("download");
  const headers: Record<string, string> = {
    "Content-Type": "video/mp4",
    "Accept-Ranges": "bytes",
    "Cache-Control": "no-store",
  };
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
