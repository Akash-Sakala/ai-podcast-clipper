import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web";
import { NextResponse } from "next/server";
import { createJob, jobDir, listJobs, startRunner } from "~/lib/jobs";

export const dynamic = "force-dynamic";

const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

/** List all jobs (newest first). The dashboard polls this. */
export async function GET() {
  return NextResponse.json(await listJobs());
}

/**
 * Create a job.
 * - JSON body `{ "youtubeUrl": "..." }`            -> YouTube job
 * - raw .mp4 body with an `x-filename` header       -> uploaded file job
 */
export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") ?? "";

  if (contentType.includes("application/json")) {
    const body = (await request.json().catch(() => null)) as {
      youtubeUrl?: string;
    } | null;
    const youtubeUrl = body?.youtubeUrl?.trim() ?? "";

    let url: URL;
    try {
      url = new URL(youtubeUrl);
    } catch {
      return NextResponse.json({ error: "Invalid URL" }, { status: 400 });
    }
    if (!YOUTUBE_HOSTS.has(url.hostname)) {
      return NextResponse.json(
        { error: "Only YouTube links are supported" },
        { status: 400 },
      );
    }

    const job = await createJob({
      source: "youtube",
      title: url.toString(),
      youtubeUrl: url.toString(),
    });
    await startRunner(job.id);
    return NextResponse.json(job, { status: 201 });
  }

  const filename = decodeURIComponent(
    request.headers.get("x-filename") ?? "video.mp4",
  );
  if (!filename.toLowerCase().endsWith(".mp4")) {
    return NextResponse.json(
      { error: "Only .mp4 files are supported" },
      { status: 400 },
    );
  }
  if (!request.body) {
    return NextResponse.json({ error: "Empty upload" }, { status: 400 });
  }

  const job = await createJob({ source: "upload", title: filename });
  const target = path.join(jobDir(job.id), "input.mp4");

  try {
    await pipeline(
      Readable.fromWeb(request.body as unknown as NodeWebReadableStream),
      fs.createWriteStream(target),
    );
  } catch (error) {
    await fsp.rm(jobDir(job.id), { recursive: true, force: true });
    return NextResponse.json(
      { error: `Upload failed: ${(error as Error).message}` },
      { status: 500 },
    );
  }

  await startRunner(job.id);
  return NextResponse.json(job, { status: 201 });
}
