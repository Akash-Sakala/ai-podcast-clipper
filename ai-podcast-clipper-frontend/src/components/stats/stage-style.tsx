import type { TimeStage } from "~/lib/stats-types";
import { cn } from "~/lib/utils";

export const STAGE_LABELS: Record<TimeStage, string> = {
  download: "Download",
  transcribe: "Transcribe",
  rank: "Rank",
  render: "Render",
  save: "Save",
  overhead: "Overhead",
};

export const STAGE_COLORS: Record<TimeStage, string> = {
  download: "bg-stage-download",
  transcribe: "bg-stage-transcribe",
  rank: "bg-stage-rank",
  render: "bg-stage-render",
  save: "bg-stage-save",
  overhead: "stage-hatch",
};

/** Label colour on each stage colour (4.5:1 or better; see --stage-*-ink). */
export const STAGE_INKS: Record<TimeStage, string> = {
  download: "text-stage-download-ink",
  transcribe: "text-stage-transcribe-ink",
  rank: "text-stage-rank-ink",
  render: "text-stage-render-ink",
  save: "text-foreground",
  overhead: "text-foreground",
};

export function Swatch({ stage }: { stage: TimeStage }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block size-2.5 rounded-[3px]", STAGE_COLORS[stage])}
    />
  );
}
