import { Loader2 } from "lucide-react";
import { clipLength } from "~/lib/format";
import type { ClipDetail } from "~/lib/job-types";

/**
 * The place of a clip that is still on its way: same size as a clip card, with its rank,
 * score and title once the ranking is known (job.json plannedClips), and what is
 * happening to it. Replaced in place by the clip card when the clip is saved.
 */
export function PendingClip({
  detail,
  rank,
  state,
}: {
  detail: ClipDetail | null;
  rank: number;
  /** "Rendering", "Saving", "Up next". */
  state: string;
}) {
  const score = detail?.score != null ? Math.round(detail.score) : null;
  const title = detail?.title?.trim() ? detail.title.trim() : null;
  const length =
    detail?.start != null && detail?.end != null
      ? clipLength(detail.end - detail.start)
      : null;
  return (
    <div className="flex flex-col gap-3">
      <div className="bg-muted shimmer relative aspect-[9/16] overflow-hidden rounded-lg">
        <span className="bg-card text-foreground absolute top-2 left-2 flex items-baseline gap-1.5 rounded-md px-2 py-1 shadow-sm">
          <span className="font-display tabular text-lg leading-none font-bold">
            #{rank}
          </span>
          {score != null && (
            <span className="tabular text-xs leading-none font-semibold opacity-80">
              {score}
            </span>
          )}
        </span>
        <span className="text-muted-foreground absolute inset-x-0 bottom-4 flex items-center justify-center gap-1.5 text-xs font-medium">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          {state}
        </span>
      </div>
      <div>
        {title ? (
          <h3 className="text-muted-foreground line-clamp-2 text-sm leading-snug font-medium">
            {title}
          </h3>
        ) : (
          <div aria-hidden className="space-y-1.5 py-0.5">
            <div className="bg-muted h-3.5 w-11/12 rounded" />
            <div className="bg-muted h-3.5 w-2/3 rounded" />
          </div>
        )}
        {length && (
          <span className="text-muted-foreground tabular mt-1 block text-xs">
            {length}
          </span>
        )}
      </div>
    </div>
  );
}
