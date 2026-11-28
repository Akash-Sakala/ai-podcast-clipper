import { Check, Download, X } from "lucide-react";
import {
  currentStage,
  duration,
  failedStepIndex,
  renderShare,
  stageSentence,
  stepsFor,
  timecode,
  transferShare,
} from "~/lib/format";
import type { Job, StageJob } from "~/lib/job-types";
import { useNow } from "~/lib/use-now";
import { cn } from "~/lib/utils";

type StepState = "done" | "current" | "todo" | "failed";

/**
 * The run's steps as a rail: a bar per step and its name under it. Every name stays
 * visible on a phone (two rows of four). Done steps are full amber with a check; the
 * current one fills with its share (or pulses when the share is unknown); a failed one
 * is red with a cross.
 */
export function StepRail({
  job,
  current,
  share = null,
  failedAt = -1,
}: {
  job: StageJob;
  /** Index of the running step (-1: none). */
  current: number;
  /** Share of the running step done (0-1), when known. */
  share?: number | null;
  /** Index of the step a failed run stopped at (-1: not failed). */
  failedAt?: number;
}) {
  const steps = stepsFor(job);
  const stateOf = (i: number): StepState => {
    if (failedAt >= 0)
      return i < failedAt ? "done" : i === failedAt ? "failed" : "todo";
    return i < current ? "done" : i === current ? "current" : "todo";
  };
  return (
    <ol
      aria-label="Progress"
      className="grid grid-cols-4 gap-x-1.5 gap-y-3 sm:flex sm:gap-1.5"
    >
      {steps.map((step, i) => {
        const state = stateOf(i);
        // One fill per step, moved with scaleX, so a step filling up and then
        // completing is one continuous movement.
        const fill =
          state === "done" || state === "failed"
            ? 1
            : state === "current" && share != null
              ? Math.max(0.08, share)
              : 0;
        return (
          <li
            key={step.stage}
            className="min-w-0 sm:flex-1"
            aria-current={state === "current" ? "step" : undefined}
          >
            <div className="bg-muted relative h-1.5 overflow-hidden rounded-full">
              {state === "current" && share == null ? (
                <span className="bg-tally tally-pulse absolute inset-0" />
              ) : (
                <span
                  className={cn(
                    "ease-move absolute inset-0 origin-left transition-transform duration-(--motion-emphasis)",
                    state === "failed" ? "bg-destructive" : "bg-tally",
                  )}
                  style={{ transform: `scaleX(${fill})` }}
                />
              )}
            </div>
            <span
              className={cn(
                "mt-1.5 flex items-start gap-1 text-xs leading-tight",
                state === "current" && "text-foreground font-medium",
                state === "failed" && "text-destructive font-medium",
                (state === "done" || state === "todo") &&
                  "text-muted-foreground",
              )}
            >
              {state === "done" && (
                <Check className="mt-px size-3 shrink-0" aria-hidden />
              )}
              {state === "failed" && (
                <X className="mt-px size-3 shrink-0" aria-hidden />
              )}
              <span>
                {step.label}
                {state === "done" && <span className="sr-only">, done</span>}
                {state === "failed" && (
                  <span className="sr-only">, failed here</span>
                )}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The same steps as the rail, without names, for a row in the video list: one short
 * bar per step (done, filling, to do). Decorative; the row's sentence says the stage.
 */
export function MiniRail({ job }: { job: StageJob }) {
  const steps = stepsFor(job);
  const current = steps.findIndex((s) => s.stage === currentStage(job));
  const share = transferShare(job) ?? renderShare(job);
  return (
    <div aria-hidden className="flex w-full gap-0.5 sm:w-40">
      {steps.map((step, i) => {
        const fill =
          i < current
            ? 1
            : i === current
              ? share != null
                ? Math.max(0.15, share)
                : null
              : 0;
        return (
          <div
            key={step.stage}
            className="bg-muted relative h-1.5 flex-1 overflow-hidden rounded-full"
          >
            {fill == null ? (
              <span className="bg-tally tally-pulse absolute inset-0" />
            ) : (
              <span
                className="bg-tally ease-move absolute inset-0 origin-left transition-transform duration-(--motion-emphasis)"
                style={{ transform: `scaleX(${fill})` }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** The YouTube video downloading on the laptop while the cloud transcribes and ranks. */
function DownloadLane({ share }: { share: number }) {
  const shown = Math.min(1, Math.max(0, share));
  return (
    <div className="text-muted-foreground flex items-center gap-2.5 text-xs">
      <Download className="size-3.5 shrink-0" aria-hidden />
      <span className="shrink-0">Downloading the video meanwhile</span>
      <div className="bg-muted h-1 max-w-48 flex-1 overflow-hidden rounded-full">
        <span
          className="bg-tally/70 ease-move block h-full origin-left transition-transform duration-(--motion-emphasis)"
          style={{ transform: `scaleX(${shown})` }}
        />
      </div>
      <span className="tabular shrink-0">{Math.round(shown * 100)}%</span>
    </div>
  );
}

export function ProgressStrip({
  job,
  action,
}: {
  job: Job;
  /** A control at the end of the status line (the video page's Cancel). */
  action?: React.ReactNode;
}) {
  // The live timer lives here, so only this strip re-renders every second.
  const now = useNow(true);
  const steps = stepsFor(job);
  const stage = currentStage(job);
  const index = steps.findIndex((s) => s.stage === stage);
  const p = job.progress;
  const startedAt = job.queuedAt ?? job.createdAt;
  const since = Math.max(0, (now - new Date(startedAt).getTime()) / 1000);
  const eta = p?.etaSeconds;
  const share = transferShare(job) ?? renderShare(job);
  const download =
    typeof p?.download === "number" && stage !== "downloading"
      ? p.download
      : null;

  return (
    <div className="space-y-3.5">
      <StepRail job={job} current={index} share={share} />
      {download != null && <DownloadLane share={download} />}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm">
        <p aria-live="polite" className="font-medium">
          {stageSentence(job, { withDownload: false })}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <p
            className="text-muted-foreground tabular"
            suppressHydrationWarning // a live clock: server and browser are a second apart
          >
            {eta != null && eta > 0
              ? `About ${duration(eta)} left`
              : timecode(since)}
          </p>
          {action}
        </div>
      </div>
    </div>
  );
}

/** Where a failed run stopped, on the same rail (nothing when the stage is unknown). */
export function FailedRail({ job }: { job: Job }) {
  const failedAt = failedStepIndex(job);
  if (failedAt < 0) return null;
  return <StepRail job={job} current={-1} failedAt={failedAt} />;
}
