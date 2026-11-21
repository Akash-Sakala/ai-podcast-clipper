import { Upload } from "lucide-react";
import { duration } from "~/lib/format";
import type { UploadState } from "./use-add-video";

function megabytes(bytes: number) {
  const mb = bytes / 1024 ** 2;
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

/**
 * The input bar while an MP4 uploads: the bar itself becomes the progress (same height
 * and place), with the bytes sent and the time left once the speed is known.
 */
export function UploadProgress({ upload }: { upload: UploadState }) {
  const { name, percent, loaded, total, startedAt } = upload;
  const seconds = (Date.now() - startedAt) / 1000;
  // The speed is only trusted after a few seconds and a few megabytes.
  const rate = seconds > 3 && loaded > 4 * 1024 ** 2 ? loaded / seconds : null;
  const left = rate ? (total - loaded) / rate : null;
  return (
    // No live region: reading out every percent would drown a screen reader. The
    // "Video added" toast announces the end.
    <div className="flex min-h-11 items-center gap-3 px-2">
      <Upload
        className="text-tally-ink dark:text-tally size-4 shrink-0"
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3 text-sm">
          <span className="truncate font-medium" title={name}>
            Uploading {name}
          </span>
          <span className="text-muted-foreground tabular shrink-0">
            {percent < 100 ? `${percent}%` : "Starting"}
          </span>
        </div>
        <div className="bg-muted mt-1.5 h-1.5 overflow-hidden rounded-full">
          <span
            className="bg-tally ease-move block h-full origin-left transition-transform duration-(--motion-emphasis)"
            style={{ transform: `scaleX(${Math.max(0.02, percent / 100)})` }}
          />
        </div>
        <p className="text-muted-foreground tabular mt-1 text-xs">
          {megabytes(loaded)} of {megabytes(total)}
          {left != null && left > 1 && percent < 100
            ? `, about ${duration(left)} left`
            : ""}
        </p>
      </div>
    </div>
  );
}
