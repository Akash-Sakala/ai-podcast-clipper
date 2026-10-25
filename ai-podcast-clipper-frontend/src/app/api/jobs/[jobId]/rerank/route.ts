import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web";
import { NextResponse } from "next/server";
import { ACTIVE_STATUSES } from "~/lib/job-types";
import {
  canRerank,
  fileFingerprint,
  inputPath,
  isValidJobId,
  jobDir,
  readJob,
  rerankNeedsFile,
  startRunner,
  updateJob,
} from "~/lib/jobs";

export const dynamic = "force-dynamic";

/**
 * Re-rank & re-render a finished job: reuses its saved transcript, so only Gemini
 * and the GPU run again (no transcription). A YouTube video is downloaded on Modal
 * again. An upload whose video was deleted after its run needs the same MP4 again,
 * sent as the raw request body (`Content-Type: video/mp4`); it is checked against
 * the job's fingerprint. The previous clips stay until the new ones are downloaded.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  if (!isValidJobId(jobId)) {
    return NextResponse.json({ error: "Invalid job id" }, { status: 400 });
  }
  const job = await readJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  if (ACTIVE_STATUSES.includes(job.status)) {
    return NextResponse.json(
      { error: "This job is still running" },
      { status: 409 },
    );
  }
  if (!canRerank(job)) {
    return NextResponse.json(
      {
        error:
          "The transcript is no longer in the job folder, so it cannot be re-ranked",
      },
      { status: 409 },
    );
  }

  const sentFile =
    (request.headers.get("content-type") ?? "").startsWith("video/") &&
    request.body;
  if (sentFile) {
    const failed = await saveVideo(jobId, job.fingerprint, sentFile);
    if (failed)
      return NextResponse.json(
        { error: failed.error },
        { status: failed.status },
      );
  } else if (rerankNeedsFile(job)) {
    return NextResponse.json(
      { error: "Add the same MP4 again to re-rank this video" },
      { status: 409 },
    );
  }

  await updateJob(jobId, {
    status: "queued",
    message: "Waiting to re-rank & re-render",
    error: null,
    progress: null,
    mode: "rerank",
    queuedAt: new Date().toISOString(),
    videoTransfer: null,
    downloadFallback: null,
    pid: undefined, // the old runner's pid is dead; the new runner records its own
  });
  await startRunner(jobId, ["--rerank"]);
  return NextResponse.json({ ok: true }, { status: 202 });
}

/** Stores the re-sent MP4 as input.mp4 if it is the job's video; returns an error otherwise. */
async function saveVideo(
  jobId: string,
  expected: string | undefined,
  body: ReadableStream<Uint8Array>,
): Promise<{ error: string; status: number } | null> {
  const tmp = path.join(jobDir(jobId), "rerank-video.tmp");
  try {
    await pipeline(
      Readable.fromWeb(body as unknown as NodeWebReadableStream),
      fs.createWriteStream(tmp),
    );
    if (expected && (await fileFingerprint(tmp)) !== expected) {
      await fsp.rm(tmp, { force: true });
      return {
        error:
          "That's a different video from the one these clips were made from",
        status: 409,
      };
    }
    await fsp.rename(tmp, inputPath(jobId));
    return null;
  } catch (error) {
    await fsp.rm(tmp, { force: true });
    return { error: `Upload failed: ${(error as Error).message}`, status: 500 };
  }
}
