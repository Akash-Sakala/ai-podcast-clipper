import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web";
import { NextResponse } from "next/server";
import { jsonWithEtag } from "~/lib/http";
import { fileFingerprint } from "~/lib/duplicates";
import { createJob, updateJob } from "~/lib/job-store";
import { listJobSummaries } from "~/lib/jobs";
import { startRunner } from "~/lib/runner";
import { markUploading } from "~/lib/stale-jobs";
import { jobDir } from "~/lib/paths";
import { parseYoutubeLink } from "~/lib/youtube";

/**
 * List all jobs (newest first) as slim summaries (see JobSummary). The dashboard
 * polls this with If-None-Match, so an unchanged list is a 304.
 */
export async function GET(request: Request) {
  return jsonWithEtag(request, await listJobSummaries());
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

    const url = parseYoutubeLink(youtubeUrl);
    if (!url) {
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

  let filename: string;
  try {
    filename = decodeURIComponent(
      request.headers.get("x-filename") ?? "video.mp4",
    );
  } catch {
    return NextResponse.json(
      { error: "The x-filename header is not valid" },
      { status: 400 },
    );
  }
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

  // The job is `queued` without a runner until the upload ends and the runner starts:
  // tell the stale-runner check that it is not abandoned.
  const uploadDone = markUploading(job.id);
  try {
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

    // Remember what this upload looks like, to recognise it if it is added again.
    const fingerprint = await fileFingerprint(target);
    if (fingerprint) await updateJob(job.id, { fingerprint });

    await startRunner(job.id);
    return NextResponse.json(job, { status: 201 });
  } finally {
    uploadDone();
  }
}
