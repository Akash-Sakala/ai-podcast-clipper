import type { LedgerRun } from "./job-types";
import { madeClips, mean, sum } from "./stats-math";
import type { ClipSourceStats } from "./stats-types";

export function clipSourceStats(runs: LedgerRun[]): ClipSourceStats | null {
  // The latest run that made clips stands for each video.
  const latest = new Map<string, LedgerRun>();
  for (const run of runs) if (madeClips(run)) latest.set(run.jobId, run);
  const youtube = [...latest.values()].filter((r) => r.source === "youtube");
  const withSources = youtube.filter((r) => r.clipSources);
  if (!withSources.length) return null;
  const graph = withSources.filter((r) => r.clipSources!.graph);
  const audiences = graph
    .map((r) => r.clipSources!.audienceAvg)
    .filter((v): v is number => typeof v === "number");
  const skipped = { sponsor: 0, introsOutros: 0, previews: 0, other: 0 };
  let sponsorVideos = 0;
  let dropped = 0;
  for (const run of withSources) {
    const s = run.clipSources!.skippedSeconds ?? {};
    if (Object.values(s).some((v) => v > 0)) sponsorVideos += 1;
    for (const [category, seconds] of Object.entries(s)) {
      if (
        category === "sponsor" ||
        category === "selfpromo" ||
        category === "interaction"
      )
        skipped.sponsor += seconds;
      else if (category === "intro" || category === "outro")
        skipped.introsOutros += seconds;
      else if (category === "preview") skipped.previews += seconds;
      else skipped.other += seconds;
    }
    dropped += run.clipSources!.droppedForSponsor ?? 0;
  }
  return {
    youtubeVideos: youtube.length,
    graphVideos: graph.length,
    clipsOnGraphVideos: sum(graph.map((r) => r.clipSources!.clips)),
    clipsOnHotspot: sum(graph.map((r) => r.clipSources!.clipsOnHotspot)),
    audienceAvg: mean(audiences),
    sponsorVideos,
    skippedSeconds: skipped,
    dropped,
  };
}
