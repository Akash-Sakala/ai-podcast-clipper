import { NextResponse } from "next/server";
import { jsonWithEtag } from "~/lib/http";
import { getJob } from "~/lib/jobs";

/** One job. The clips page polls this while the job runs (If-None-Match: 304 when unchanged). */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const job = await getJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  return jsonWithEtag(request, job);
}
