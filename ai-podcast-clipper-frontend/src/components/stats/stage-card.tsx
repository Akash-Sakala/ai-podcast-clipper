import { type Money, stepTime } from "~/lib/format";
import type { CostStage } from "~/lib/job-types";
import {
  COST_STAGES,
  type StageBreakdown,
  type Stats,
  TIME_STAGES,
  type TimeStage,
} from "~/lib/stats-types";
import { cn } from "~/lib/utils";
import { Card } from "../ui/card";
import { STAGE_COLORS, STAGE_INKS, STAGE_LABELS, Swatch } from "./stage-style";

function compactTime(seconds: number) {
  const s = Math.round(seconds);
  return s < 60
    ? `${s}s`
    : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function StageBar({
  values,
  max,
  label,
  total,
  format,
}: {
  values: [TimeStage, number][];
  max: number;
  label: string;
  total: string;
  format: (stage: TimeStage, value: number) => string;
}) {
  const sumValues = values.reduce((a, [, v]) => a + v, 0);
  const shown = values.filter(([, v]) => v > 0);
  return (
    <div className="grid grid-cols-[3.75rem_1fr_auto] items-center gap-3">
      <span className="text-muted-foreground text-xs">{label}</span>
      <div className="min-w-0">
        <div
          className="flex h-6 gap-[2px]"
          style={{ width: `${max ? (sumValues / max) * 100 : 0}%` }}
          role="img"
          aria-label={`${label}: ${shown.map(([k, v]) => `${STAGE_LABELS[k]} ${format(k, v)}`).join(", ")}`}
        >
          {shown.map(([stage, value], i) => {
            const share = value / sumValues;
            // Each segment is a size container: it names its stage when it is wide
            // enough (so the bar reads without the colours), else shows the value
            // alone, else nothing (the legend and the tooltip still name it).
            return (
              <span
                key={stage}
                title={`${STAGE_LABELS[stage]}: ${format(stage, value)}`}
                className={cn(
                  "@container h-full min-w-[3px] overflow-hidden",
                  STAGE_COLORS[stage],
                  STAGE_INKS[stage],
                  i === shown.length - 1 && "rounded-r-[4px]",
                )}
                style={{ width: `${share * 100}%` }}
              >
                <span className="hidden h-full items-center justify-center gap-1 text-[11px] font-semibold whitespace-nowrap @min-[2.5rem]:flex">
                  <span className="hidden font-medium @min-[7.5rem]:inline">
                    {STAGE_LABELS[stage]}
                  </span>
                  {format(stage, value)}
                </span>
              </span>
            );
          })}
        </div>
      </div>
      <span className="tabular w-[5.5rem] text-right text-sm font-semibold">
        {total}
      </span>
    </div>
  );
}

export function StageCard({
  stats,
  m,
}: {
  stats: Stats;
  m: (v: Money | null) => string;
}) {
  const rows = (["new", "rerank"] as const)
    .map((key) => [key, stats.stages[key]] as const)
    .filter(
      (entry): entry is readonly ["new" | "rerank", StageBreakdown] =>
        entry[1] !== null,
    );
  if (!rows.length) return null;
  const timeMax = Math.max(...rows.map(([, b]) => b.totalSeconds));
  const costRows = rows.filter(([, b]) => b.cost);
  const costMax = Math.max(
    0,
    ...costRows.map(([, b]) => b.totalCost?.usd ?? 0),
  );
  const label = (key: "new" | "rerank") => (key === "new" ? "New" : "Re-rank");
  return (
    <Card title="By stage" description="An average run, start to finish.">
      <div className="grid gap-6 md:grid-cols-2 md:gap-8">
        <div className="space-y-2.5">
          <h3 className="text-sm font-medium">Time</h3>
          {rows.map(([key, b]) => (
            <StageBar
              key={key}
              label={label(key)}
              max={timeMax}
              total={stepTime(b.totalSeconds)}
              values={TIME_STAGES.map((k) => [k, b.time[k]])}
              format={(_, v) => compactTime(v)}
            />
          ))}
        </div>
        <div className="space-y-2.5">
          <h3 className="text-sm font-medium">Cost</h3>
          {costRows.length ? (
            costRows.map(([key, b]) => (
              <StageBar
                key={key}
                label={label(key)}
                max={costMax}
                total={m(b.totalCost)}
                values={COST_STAGES.map((k) => [k, b.cost![k].usd])}
                format={(stage) => m(b.cost![stage as CostStage])}
              />
            ))
          ) : (
            <p className="text-muted-foreground text-sm">
              Stage costs appear after the next run.
            </p>
          )}
        </div>
      </div>
      <div className="text-muted-foreground mt-4 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        {TIME_STAGES.map((stage) => (
          <span key={stage} className="inline-flex items-center gap-1.5">
            <Swatch stage={stage} />
            {STAGE_LABELS[stage]}
          </span>
        ))}
      </div>
      <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
        Render runs one GPU per clip at the same time: its time is the slowest
        clip, its cost is all of them. Download (with the upload) and Save run
        on your laptop, so they cost nothing. Overhead is mostly Modal
        containers starting.
      </p>
    </Card>
  );
}
