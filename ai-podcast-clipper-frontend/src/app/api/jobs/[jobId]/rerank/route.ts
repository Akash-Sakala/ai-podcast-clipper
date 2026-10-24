import { NextResponse } from "next/server";
import { ACTIVE_STATUSES } from "~/lib/job-types";
import {
  canRerank,
  isValidJobId,
  readJob,
  startRunner,
  updateJob,
} from "~/lib/jobs";

export const dynamic = "force-dynamic";

/**
 * Re-rank & re-render a finished job: reuses its saved video and transcript, so
 * only Gemini and the GPU run again (no download, upload of a new video or
 * transcription). The previous clips stay until the new ones are downloaded.
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
          "The video or transcript is no longer in the job folder, so it cannot be re-ranked",
      },
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
    pid: undefined, // the old runner's pid is dead; the new runner records its own
  });
  await startRunner(jobId, ["--rerank"]);
  return NextResponse.json({ ok: true }, { status: 202 });
}
