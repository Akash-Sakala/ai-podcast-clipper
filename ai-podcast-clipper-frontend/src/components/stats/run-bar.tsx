import { useEffect, useId, useRef, useState } from "react";
import { type Money, stepTime } from "~/lib/format";
import type { CostStage } from "~/lib/job-types";
import { type RunRow, TIME_STAGES } from "~/lib/stats-types";
import { cn } from "~/lib/utils";
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
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const tipId = useId();
  // The accessible name carries the per-stage times for screen readers.
  const summary = shown
    .map((k) => `${STAGE_LABELS[k]} ${stepTime(run.time[k])}`)
    .join(", ");

  // A tap or click toggles the tooltip (hover and keyboard focus show it too);
  // a press outside or Escape closes it.
  useEffect(() => {
    if (!open) return;
    const onPress = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPress);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPress);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span
      ref={root}
      className="group relative inline-flex w-24 shrink-0 align-middle sm:w-28"
    >
      <button
        type="button"
        aria-label={`Stages: ${summary}`}
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        onClick={() => setOpen((value) => !value)}
        onBlur={(e) => {
          if (!root.current?.contains(e.relatedTarget as Node | null))
            setOpen(false);
        }}
        className="flex w-full rounded-sm py-1.5 text-left"
      >
        <span
          className="flex h-2 gap-px"
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
      </button>
      <span
        id={tipId}
        role="tooltip"
        className={cn(
          "bg-popover text-popover-foreground pop-in pointer-events-none absolute bottom-full left-0 z-10 mb-1 origin-bottom-left rounded-md border px-3 py-2 text-xs shadow-md group-hover:block group-has-focus-visible:block",
          open ? "block" : "hidden",
        )}
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
      </span>
    </span>
  );
}
