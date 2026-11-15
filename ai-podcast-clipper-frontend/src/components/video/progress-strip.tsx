import {
  currentStage,
  duration,
  renderShare,
  stageSentence,
  stepsFor,
  timecode,
  transferShare,
} from "~/lib/format";
import type { Job } from "~/lib/job-types";
import { useNow } from "~/lib/use-now";
import { cn } from "~/lib/utils";

export function ProgressStrip({ job }: { job: Job }) {
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

  return (
    <div className="space-y-3">
      <ol className="flex gap-1.5" aria-label="Progress">
        {steps.map((step, i) => {
          const state = i < index ? "done" : i === index ? "current" : "todo";
          const fill =
            state === "current" && share != null
              ? Math.max(8, share * 100)
              : null;
          return (
            <li key={step.stage} className="min-w-0 flex-1">
              <div className="bg-muted relative h-1.5 overflow-hidden rounded-full">
                {state === "done" && (
                  <span className="bg-tally absolute inset-0" />
                )}
                {state === "current" && (
                  <span
                    className={cn(
                      "bg-tally absolute inset-y-0 left-0 transition-[width] duration-500",
                      fill == null && "tally-pulse w-full",
                    )}
                    style={fill != null ? { width: `${fill}%` } : undefined}
                  />
                )}
              </div>
              <span
                className={cn(
                  "mt-1.5 block truncate text-xs",
                  state === "current"
                    ? "text-foreground font-medium"
                    : "text-muted-foreground hidden sm:block",
                )}
                aria-current={state === "current" ? "step" : undefined}
              >
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <p aria-live="polite" className="font-medium">
          {stageSentence(job)}
        </p>
        <p
          className="text-muted-foreground tabular shrink-0"
          suppressHydrationWarning // a live clock: server and browser are a second apart
        >
          {eta != null && eta > 0
            ? `About ${duration(eta)} left`
            : timecode(since)}
        </p>
      </div>
    </div>
  );
}
