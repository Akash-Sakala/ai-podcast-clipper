import { NextResponse } from "next/server";
import { readJob } from "~/lib/job-store";
import { isActive } from "~/lib/job-types";
import { isValidJobId } from "~/lib/paths";
import { cancelRunner } from "~/lib/runner";
import { isCancelling, markCancelling } from "~/lib/stale-jobs";

/**
 * Cancel a running video: runner.py --cancel stops its runner, cancels what it started
 * on Modal and removes its files from the volume. Answers once that is done (a few
 * seconds). Not while the clips are being saved: the run is all but over by then, and
 * stopping a re-rank mid-swap could leave its clip folders half changed.
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
  if (!isActive(job)) {
    return NextResponse.json(
      { error: "This video is not running" },
      { status: 409 },
    );
  }
  if (job.progress?.stage === "saving") {
    return NextResponse.json(
      { error: "The clips are being saved, so it's nearly done" },
      { status: 409 },
    );
  }
  if (isCancelling(jobId)) {
    return NextResponse.json({ ok: true }, { status: 202 });
  }

  const done = markCancelling(jobId);
  try {
    const ok = await cancelRunner(jobId);
    if (!ok) {
      return NextResponse.json(
        { error: "Couldn't stop it. See runner.log in the video's folder." },
        { status: 500 },
      );
    }
    return NextResponse.json({ ok: true });
  } finally {
    done();
  }
}
