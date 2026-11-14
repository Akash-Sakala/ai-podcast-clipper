import { NextResponse } from "next/server";
import { loadStats } from "~/lib/stats-server";

export const dynamic = "force-dynamic";

/** Stats for ?period=month (default) or ?period=all. The stats page polls this. */
export async function GET(request: Request) {
  const period =
    new URL(request.url).searchParams.get("period") === "all" ? "all" : "month";
  return NextResponse.json(await loadStats(period));
}
