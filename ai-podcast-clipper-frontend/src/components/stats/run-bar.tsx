import { type Money, stepTime } from "~/lib/format";
import type { CostStage } from "~/lib/job-types";
import { type RunRow, TIME_STAGES } from "~/lib/stats-types";
import { cn } from "~/lib/utils";
import { BarPopover } from "./bar-popover";
import { STAGE_COLORS, STAGE_LABELS, Swatch } from "./stage-style";

export function RunBar({
  run,
  max,
  m,
}: {
  run: RunRow;
  max: number;
  m: (v: Money | null) => string;
}) {
  const shown = TIME_STAGES.filter((k) => run.time[k] > 0);
  const total = shown.reduce((a, k) => a + run.time[k], 0);
  // The accessible name carries the per-stage times for screen readers.
  const summary = shown
    .map((k) => `${STAGE_LABELS[k]} ${stepTime(run.time[k])}`)
    .join(", ");

  return (
    <BarPopover
      label={`Stages: ${summary}`}
      className="w-24 shrink-0 align-middle sm:w-28"
      buttonClassName="py-1.5 pointer-coarse:py-4"
      bar={
        <span
          className="ease-move flex h-2 gap-px transition-[width] duration-(--motion-emphasis)"
          style={{ width: `${max ? (total / max) * 100 : 0}%` }}
          aria-hidden
        >
          {shown.map((k, i) => (
            <span
              key={k}
              className={cn(
                "h-full",
                STAGE_COLORS[k],
                i === shown.length - 1 && "rounded-r-[3px]",
              )}
              style={{ flexGrow: run.time[k], flexBasis: 0 }}
            />
          ))}
        </span>
      }
    >
      <table className="tabular">
        <thead>
          <tr className="text-muted-foreground">
            <th className="pr-4 text-left font-normal">Stage</th>
            <th className="pr-4 text-right font-normal">Time</th>
            <th className="text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((k) => (
            <tr key={k}>
              <td className="pr-4 whitespace-nowrap">
                <span className="inline-flex items-center gap-1.5">
                  <Swatch stage={k} />
                  {STAGE_LABELS[k]}
                </span>
              </td>
              <td className="pr-4 text-right whitespace-nowrap">
                {stepTime(run.time[k])}
              </td>
              <td className="text-right whitespace-nowrap">
                {run.stageCost && k in run.stageCost
                  ? m(run.stageCost[k as CostStage])
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </BarPopover>
  );
}
