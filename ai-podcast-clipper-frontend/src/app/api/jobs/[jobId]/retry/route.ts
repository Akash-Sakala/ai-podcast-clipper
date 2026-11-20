import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { readJob, updateJob } from "~/lib/job-store";
import { startRunner } from "~/lib/runner";
import { isValidJobId, jobDir } from "~/lib/paths";
import { keptVideoPath } from "~/lib/video-store";

/**
 * Run a failed job again from the start. A failed job keeps its video (an upload in
 * the job folder, a YouTube download in the laptop's temp folder, or a video kept
 * after an earlier run), so it is reused; otherwise the runner downloads the YouTube
 * video again on the laptop.
 */
export async function POST(
  _request: Request,
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
  if (job.status !== "failed") {
    return NextResponse.json(
      { error: "Only failed jobs can be retried" },
      { status: 409 },
    );
  }
  const hasVideo =
    fs.existsSync(path.join(jobDir(jobId), "input.mp4")) ||
    keptVideoPath(jobId) !== null;
  if (job.source === "upload" && !hasVideo) {
    return NextResponse.json(
      { error: "The uploaded video is no longer in the job folder" },
      { status: 409 },
    );
  }

  await updateJob(jobId, {
    status: "queued",
    message: "Waiting to start",
    error: null,
    progress: null,
    mode: "full",
    queuedAt: new Date().toISOString(),
    videoTransfer: null,
    pid: undefined,
  });
  await startRunner(jobId);
  return NextResponse.json({ ok: true }, { status: 202 });
}
