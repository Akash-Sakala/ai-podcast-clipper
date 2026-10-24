import { NextResponse } from "next/server";
import { getJob } from "~/lib/jobs";

export const dynamic = "force-dynamic";

/** One job. The clips page polls this while the job runs. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const job = await getJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  return NextResponse.json(job);
}
