import { jsonWithEtag } from "~/lib/http";
import { loadStats } from "~/lib/stats-server";

/**
 * Stats for ?period=month (default) or ?period=all, listing the newest ?runs=N runs
 * (default 20, at most 1000). The stats page polls this with If-None-Match.
 */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const period = params.get("period") === "all" ? "all" : "month";
  const asked = Number.parseInt(params.get("runs") ?? "", 10);
  const runs = Number.isFinite(asked)
    ? Math.min(Math.max(asked, 1), 1000)
    : undefined;
  return jsonWithEtag(request, await loadStats(period, runs));
}
