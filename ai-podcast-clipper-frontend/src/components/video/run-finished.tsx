import { Check } from "lucide-react";
import { m } from "motion/react";
import { stepTime } from "~/lib/format";
import { popIn } from "~/lib/motion";

/** "5 clips ready in 6 min 12 s": what the progress strip becomes when the run ends here. */
export function RunFinished({
  clips,
  seconds,
}: {
  clips: number;
  seconds: number | null;
}) {
  return (
    <m.div
      role="status"
      variants={popIn}
      initial="hidden"
      animate="shown"
      exit="exit"
      className="bg-card flex origin-top items-center gap-3 rounded-xl border p-4 sm:p-5"
    >
      <span className="bg-tally text-tally-ink inline-flex size-8 shrink-0 items-center justify-center rounded-full">
        <Check className="size-4" strokeWidth={2.5} aria-hidden />
      </span>
      <p className="text-sm">
        <span className="font-medium">{readyText(clips)}</span>
        {seconds != null && (
          <span className="text-muted-foreground"> in {stepTime(seconds)}</span>
        )}
      </p>
    </m.div>
  );
}

export function readyText(clips: number) {
  return clips === 1 ? "1 clip ready" : `${clips} clips ready`;
}
