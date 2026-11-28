import { NextResponse } from "next/server";
import { updateJob } from "~/lib/job-store";
import { getJob } from "~/lib/jobs";
import { isValidJobId } from "~/lib/paths";
import { startRunner } from "~/lib/runner";

/**
 * Render again: the moments the last run picked, rendered once more (runner.py
 * --render-again), without transcribing or asking Gemini. Offered when a finished job has
 * no clips, for instance after every clip failed to render.
 */
export async function POST(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  if (!isValidJobId(jobId)) {
    return NextResponse.json({ error: "Invalid job id" }, { status: 400 });
  }
  const job = await getJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  if (!job.canRenderAgain) {
    return NextResponse.json(
      {
        error:
          "Only a finished video without clips, whose moments are saved, can be rendered again",
      },
      { status: 409 },
    );
  }
  await updateJob(jobId, {
    status: "queued",
    message: "Waiting to start",
    error: null,
    progress: null,
    mode: "render",
    queuedAt: new Date().toISOString(),
    videoTransfer: null,
    pid: undefined,
  });
  await startRunner(jobId, ["--render-again"]);
  return NextResponse.json({ ok: true }, { status: 202 });
}
