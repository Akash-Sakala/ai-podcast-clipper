"use client";

import { timecode } from "~/lib/format";
import type { ClipDetail, JobReplay } from "~/lib/job-types";
import { cn } from "~/lib/utils";

const WIDTH = 1000;
const HEIGHT = 64;
const TOP = 4;
/** Rank chips closer than this share of the width go on a second row. */
const CHIP_GAP = 0.06;

/** The wave above YouTube's progress bar, drawn as a smooth area through the segment centres. */
function wavePath(heatmap: JobReplay["heatmap"], total: number) {
  const points = heatmap.map(([start, end, value]): [number, number] => [
    ((start + end) / 2 / total) * WIDTH,
    HEIGHT - TOP - Math.max(0, Math.min(1, value)) * (HEIGHT - TOP * 2),
  ]);
  if (points.length < 2) return "";
  const [first] = points;
  let d = `M0 ${HEIGHT} L0 ${first![1]} L${first![0]} ${first![1]}`;
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1]!;
    const [x1, y1] = points[i]!;
    const mx = (x0 + x1) / 2;
    d += ` C${mx} ${y0} ${mx} ${y1} ${x1} ${y1}`;
  }
  const last = points[points.length - 1]!;
  return `${d} L${WIDTH} ${last[1]} L${WIDTH} ${HEIGHT} Z`;
}

const SKIP_LABELS: Record<string, string> = {
  sponsor: "Sponsor read",
  selfpromo: "Self-promotion",
  interaction: "Subscribe reminder",
  intro: "Intro",
  outro: "Outro",
  preview: "Preview of a later part",
};

export function ReplayWave({
  replay,
  clips,
  dimmed = false,
}: {
  replay: JobReplay;
  clips: ClipDetail[];
  dimmed?: boolean;
}) {
  const total =
    replay.duration ?? replay.heatmap[replay.heatmap.length - 1]?.[1] ?? 0;
  if (!replay.available || replay.heatmap.length < 10 || total <= 0)
    return null;

  const spans = clips
    .filter((c) => c.start != null && c.end != null)
    .map((c) => ({
      name: c.name.replace(/\.mp4$/, ""),
      rank: c.rank,
      start: c.start!,
      end: c.end!,
      onPeak: !!c.peak,
    }))
    .sort((a, b) => a.start + a.end - (b.start + b.end));
  // Alternate rows only where chips would collide.
  const rowEnds = [-1, -1];
  const chips = spans.map((s) => {
    const at = (s.start + s.end) / 2 / total;
    const row =
      at - rowEnds[0]! >= CHIP_GAP || at - rowEnds[1]! < CHIP_GAP ? 0 : 1;
    rowEnds[row] = at;
    return { ...s, at, row };
  });
  const twoRows = chips.some((c) => c.row === 1);
  const skips = replay.sponsor?.segments ?? [];

  return (
    <section
      aria-label="Most replayed"
      className={cn(
        "bg-card rounded-xl border p-4 sm:p-5",
        dimmed && "opacity-50",
      )}
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-medium">Most replayed</h2>
        <p className="text-muted-foreground text-xs">
          How often YouTube viewers rewatch each part, with your clips marked
        </p>
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="text-foreground block h-16 w-full"
        role="img"
        aria-label={`Replay graph of the video with ${spans.length} clips marked`}
      >
        {spans.map((s) => (
          <rect
            key={s.name}
            x={(s.start / total) * WIDTH}
            y={0}
            width={Math.max(((s.end - s.start) / total) * WIDTH, 2)}
            height={HEIGHT}
            className="fill-tally"
            opacity={0.35}
          >
            <title>{`#${s.rank}, ${timecode(s.start)} to ${timecode(s.end)}`}</title>
          </rect>
        ))}
        <path
          d={wavePath(replay.heatmap, total)}
          fill="currentColor"
          fillOpacity={0.16}
          stroke="currentColor"
          strokeOpacity={0.55}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
        {skips.map((s) => (
          <rect
            key={`${s.category}-${s.start}`}
            x={(s.start / total) * WIDTH}
            y={HEIGHT - 4}
            width={Math.max(((s.end - s.start) / total) * WIDTH, 2)}
            height={4}
            className="fill-muted-foreground"
          >
            <title>{`${SKIP_LABELS[s.category] ?? "Skipped"}, ${timecode(s.start)} to ${timecode(s.end)} (left out, SponsorBlock)`}</title>
          </rect>
        ))}
      </svg>

      <div className={cn("relative mt-1.5", twoRows ? "h-12" : "h-6")}>
        {chips.map((c) => (
          <a
            key={c.name}
            href={`#${c.name}`}
            title={`#${c.rank}, ${timecode(c.start)} to ${timecode(c.end)}${c.onPeak ? ", on a replay peak" : ""}`}
            className={cn(
              "tabular absolute flex h-5 min-w-5 -translate-x-1/2 items-center justify-center rounded-md px-1 text-xs leading-none font-bold shadow-sm",
              c.onPeak
                ? "bg-tally text-tally-ink"
                : "bg-card text-foreground border",
            )}
            style={{
              left: `${Math.min(97, Math.max(3, c.at * 100))}%`,
              top: c.row ? "1.5rem" : 0,
            }}
          >
            {c.rank}
          </a>
        ))}
      </div>

      <div className="text-muted-foreground tabular mt-1 flex justify-between text-xs">
        <span>0:00</span>
        {skips.length > 0 && (
          <span className="flex items-center gap-1.5">
            <span className="bg-muted-foreground inline-block h-1 w-3 rounded-full" />
            Left out (SponsorBlock)
          </span>
        )}
        <span>{timecode(total)}</span>
      </div>
    </section>
  );
}
