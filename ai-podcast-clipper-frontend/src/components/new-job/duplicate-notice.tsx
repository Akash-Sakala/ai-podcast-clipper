import { ListRestart, Loader2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ACTIVE_STATUSES, type JobStatus } from "~/lib/job-types";

export type Duplicate = {
  id: string;
  title: string;
  status: JobStatus;
  clips: number;
  canRerank: boolean;
  /** An upload whose video was deleted: re-rank sends the dropped file again. */
  rerankNeedsFile: boolean;
  hasThumbnail: boolean;
};

export function DuplicateNotice({
  duplicate,
  file,
  onAgain,
  onRerankWithFile,
  onClose,
}: {
  duplicate: Duplicate;
  /** The dropped file, when the duplicate was found by its fingerprint. */
  file: File | null;
  onAgain: () => void;
  onRerankWithFile: (file: File) => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [reranking, setReranking] = useState(false);
  const href = `/videos/${duplicate.id}`;
  const active = ACTIVE_STATUSES.includes(duplicate.status);
  const failed = duplicate.status === "failed";
  const title = duplicate.title.replace(/^https?:\/\/(www\.)?/, "");

  const canRerank =
    duplicate.canRerank && (!duplicate.rerankNeedsFile || file !== null);

  const rerank = async () => {
    if (duplicate.rerankNeedsFile && file) return onRerankWithFile(file);
    setReranking(true);
    try {
      const response = await fetch(`/api/jobs/${duplicate.id}/rerank`, {
        method: "POST",
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(body.error ?? `Request failed (${response.status})`);
      }
      router.push(href);
    } catch (error) {
      toast.error("Couldn't re-rank", {
        description: (error as Error).message,
      });
      setReranking(false);
    }
  };

  const message = active
    ? "This video is being processed right now."
    : failed
      ? "This video was added before, but it failed."
      : duplicate.clips
        ? `You already clipped this video: ${duplicate.clips} ${duplicate.clips === 1 ? "clip is" : "clips are"} ready.`
        : "You already processed this video, but it produced no clips.";
  const secondary =
    "text-foreground/80 hover:text-foreground hover:bg-background/60 inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium whitespace-nowrap transition-colors disabled:opacity-60";

  return (
    <div
      role="status"
      className="bg-tally-soft mx-1 mt-2 mb-1 flex flex-col gap-3 rounded-lg p-3 sm:flex-row sm:items-center"
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        {duplicate.hasThumbnail && (
          // eslint-disable-next-line @next/next/no-img-element -- local API image
          <img
            src={`/api/media/${duplicate.id}/thumbnail`}
            alt=""
            className="aspect-video w-16 shrink-0 rounded object-cover"
          />
        )}
        <div className="min-w-0 text-sm">
          <p className="font-medium">{message}</p>
          <p className="text-muted-foreground truncate" title={duplicate.title}>
            {title}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-1">
        <Link
          href={href}
          className="bg-tally text-tally-ink hover:bg-tally/90 inline-flex h-8 items-center rounded-md px-3 text-sm font-semibold whitespace-nowrap transition-colors"
        >
          {active
            ? "Show progress"
            : duplicate.clips
              ? "Open clips"
              : "Open video"}
        </Link>
        {!active && canRerank && (
          <button
            type="button"
            onClick={rerank}
            disabled={reranking}
            className={secondary}
          >
            {reranking ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ListRestart className="size-4" />
            )}
            Re-rank
          </button>
        )}
        {!active && (
          <button type="button" onClick={onAgain} className={secondary}>
            Make clips again
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="Dismiss"
          className="text-muted-foreground hover:text-foreground inline-flex size-8 items-center justify-center rounded-md"
        >
          <X className="size-4" />
        </button>
      </div>
    </div>
  );
}
