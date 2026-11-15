import { plural } from "~/lib/format";
import type { Stats } from "~/lib/stats-types";
import { Card } from "../ui/card";

function minutes(seconds: number) {
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

export function ClipSourcesCard({ stats }: { stats: Stats }) {
  const c = stats.clipSources!;
  const skipped = c.skippedSeconds;
  const totalSkipped =
    skipped.sponsor + skipped.introsOutros + skipped.previews + skipped.other;
  const parts = [
    skipped.sponsor > 0 && `sponsor reads ${minutes(skipped.sponsor)}`,
    skipped.introsOutros > 0 &&
      `intros and outros ${minutes(skipped.introsOutros)}`,
    skipped.previews > 0 && `previews ${minutes(skipped.previews)}`,
    skipped.other > 0 && `other ${minutes(skipped.other)}`,
  ].filter(Boolean);
  return (
    <Card
      title="Clip sources"
      description="How YouTube viewers' data shaped the clips."
    >
      <div className="space-y-2.5 text-sm leading-relaxed">
        <p>
          <span className="font-medium">Most replayed.</span>{" "}
          {c.graphVideos
            ? `${c.graphVideos} of ${plural(c.youtubeVideos, "YouTube video")} had YouTube's Most replayed graph. ${c.clipsOnHotspot} of their ${c.clipsOnGraphVideos} clips came from a replay peak${c.audienceAvg !== null ? `, with an average audience score of ${Math.round(c.audienceAvg)} (50 is a typical moment)` : ""}.`
            : `None of the ${plural(c.youtubeVideos, "YouTube video")} had YouTube's Most replayed graph yet.`}
        </p>
        <p>
          <span className="font-medium">SponsorBlock.</span>{" "}
          {totalSkipped > 0
            ? `${minutes(totalSkipped)} skipped in ${plural(c.sponsorVideos, "video")}: ${parts.join(", ")}.${c.dropped ? ` That ruled out ${plural(c.dropped, "clip candidate")}.` : ""}`
            : "No sponsor reads, intros or previews were marked in these videos."}
        </p>
      </div>
    </Card>
  );
}
