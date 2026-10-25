import { NextResponse } from "next/server";
import { listJobs } from "~/lib/jobs";
import { computeStats } from "~/lib/stats";

export const dynamic = "force-dynamic";

/** Cost and time stats across all videos. The stats page polls this. */
export async function GET() {
  return NextResponse.json(computeStats(await listJobs()));
}
