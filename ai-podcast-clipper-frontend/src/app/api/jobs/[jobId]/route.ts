import { NextResponse } from "next/server";
import { jsonWithEtag } from "~/lib/http";
import { getJob } from "~/lib/jobs";

/** One job. Its page reads this (lib/job-sync.ts), every 3 s while it runs (If-None-Match: 304 when unchanged). */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const servedAt = Date.now();
  const job = await getJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  return jsonWithEtag(request, job, servedAt);
}
