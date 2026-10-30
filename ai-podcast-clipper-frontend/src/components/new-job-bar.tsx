"use client";

import { Link2, ListRestart, Loader2, Upload, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import { fingerprintFile } from "~/lib/fingerprint";
import { ACTIVE_STATUSES, type JobStatus } from "~/lib/job-types";
import { uploadVideo } from "~/lib/upload";
import { cn } from "~/lib/utils";

type Duplicate = {
  id: string;
  title: string;
  status: JobStatus;
  clips: number;
  canRerank: boolean;
  /** An upload whose video was deleted: re-rank sends the dropped file again. */
  rerankNeedsFile: boolean;
  hasThumbnail: boolean;
};

type Pending = { kind: "url"; url: string } | { kind: "file"; file: File };

async function findDuplicate(query: {
  youtubeUrl?: string;
  fingerprint?: string;
}): Promise<Duplicate | null> {
  try {
    const response = await fetch("/api/jobs/duplicate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
    });
    if (!response.ok) return null;
    return ((await response.json()) as { duplicate: Duplicate | null })
      .duplicate;
  } catch {
    return null; // never block adding a video because the check failed
  }
}

function DuplicateNotice({
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

const YOUTUBE =
  /^(https?:\/\/)?((www|m|music)\.)?(youtube\.com|youtu\.be)\/.+/i;
const MAX_BYTES = 4 * 1024 * 1024 * 1024;

export function NewJobBar({ onCreated }: { onCreated: () => Promise<void> }) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [upload, setUpload] = useState<{
    name: string;
    percent: number;
  } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [checking, setChecking] = useState(false);
  const [duplicate, setDuplicate] = useState<{
    job: Duplicate;
    pending: Pending;
  } | null>(null);
  const busy = submitting || checking || upload !== null;

  /** Uploads a new video, or (rerankJobId) the same video again to re-rank its job. */
  const startUpload = async (file: File, rerankJobId?: string) => {
    setUpload({ name: file.name, percent: 0 });
    try {
      await uploadVideo(
        rerankJobId ? `/api/jobs/${rerankJobId}/rerank` : "/api/jobs",
        file,
        (percent) => setUpload({ name: file.name, percent }),
      );
      toast.success(rerankJobId ? "Re-ranking" : "Video added", {
        description: rerankJobId
          ? "Reusing the transcript. The current clips stay until the new ones are ready."
          : "Clips will appear here when they're ready.",
      });
      if (rerankJobId) router.push(`/videos/${rerankJobId}`);
      else await onCreated();
    } catch (error) {
      toast.error(rerankJobId ? "Couldn't re-rank" : "Upload failed", {
        description: (error as Error).message,
      });
    } finally {
      setUpload(null);
    }
  };

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop: (accepted) => {
      if (accepted[0]) void checkFile(accepted[0]);
    },
    onDropRejected: () =>
      toast.error("That file can't be used", {
        description: "Choose one .mp4 file up to 4 GB.",
      }),
    accept: { "video/mp4": [".mp4"] },
    maxSize: MAX_BYTES,
    maxFiles: 1,
    noClick: true,
    noKeyboard: true,
    disabled: busy,
  });

  // Before adding anything, check whether this video was processed already.
  const checkFile = async (file: File) => {
    setDuplicate(null);
    setChecking(true);
    const fingerprint = await fingerprintFile(file).catch(() => null);
    const job = fingerprint ? await findDuplicate({ fingerprint }) : null;
    setChecking(false);
    if (job) setDuplicate({ job, pending: { kind: "file", file } });
    else await startUpload(file);
  };

  const submitUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = url.trim();
    if (!value) return;
    if (!YOUTUBE.test(value)) {
      setInvalid(true);
      return;
    }
    setDuplicate(null);
    setChecking(true);
    const job = await findDuplicate({ youtubeUrl: value });
    setChecking(false);
    if (job) {
      setDuplicate({ job, pending: { kind: "url", url: value } });
      return;
    }
    await addUrl(value);
  };

  const addAgain = () => {
    if (!duplicate) return;
    const { pending } = duplicate;
    setDuplicate(null);
    if (pending.kind === "file") void startUpload(pending.file);
    else void addUrl(pending.url);
  };

  const addUrl = async (value: string) => {
    setSubmitting(true);
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ youtubeUrl: value }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(body.error ?? `Request failed (${response.status})`);
      }
      setUrl("");
      toast.success("Video added", {
        description: "Clips will appear here when they're ready.",
      });
      await onCreated();
    } catch (error) {
      toast.error("Couldn't add this video", {
        description: (error as Error).message,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      {...getRootProps()}
      className={cn(
        "bg-card relative rounded-xl border p-2 transition-colors",
        isDragActive && "border-tally ring-tally/30 ring-4",
      )}
    >
      <input {...getInputProps()} aria-label="Upload an MP4 file" />

      {upload ? (
        <div className="flex h-11 items-center gap-3 px-2">
          <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate font-medium">{upload.name}</span>
              <span className="text-muted-foreground tabular shrink-0">
                {upload.percent < 100 ? `${upload.percent}%` : "Starting"}
              </span>
            </div>
            <div className="bg-muted mt-1.5 h-1 overflow-hidden rounded-full">
              <span
                className="bg-tally block h-full transition-[width]"
                style={{ width: `${upload.percent}%` }}
              />
            </div>
          </div>
        </div>
      ) : (
        <form onSubmit={submitUrl} className="flex flex-col gap-2 sm:flex-row">
          <label className="relative flex min-w-0 flex-1 items-center">
            <span className="sr-only">YouTube link</span>
            <Link2 className="text-muted-foreground pointer-events-none absolute left-3 size-4" />
            <input
              type="url"
              inputMode="url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setInvalid(false);
              }}
              placeholder="Paste a YouTube link"
              aria-invalid={invalid || undefined}
              aria-describedby="new-job-hint"
              disabled={busy}
              className="placeholder:text-muted-foreground h-11 w-full rounded-lg bg-transparent pr-9 pl-9 text-base outline-none focus-visible:outline-none sm:text-sm"
            />
            {url && (
              <button
                type="button"
                onClick={() => {
                  setUrl("");
                  setInvalid(false);
                }}
                aria-label="Clear link"
                className="text-muted-foreground hover:text-foreground absolute right-2 inline-flex size-7 items-center justify-center rounded-md"
              >
                <X className="size-4" />
              </button>
            )}
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={open}
              disabled={busy}
              className="text-muted-foreground hover:text-foreground hover:bg-accent inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition-colors disabled:opacity-60 sm:flex-none"
            >
              <Upload className="size-4" />
              Upload MP4
            </button>
            <button
              type="submit"
              disabled={busy || !url.trim()}
              className="bg-tally text-tally-ink hover:bg-tally/90 disabled:bg-muted disabled:text-muted-foreground inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors sm:flex-none"
            >
              {(submitting || checking) && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Make clips
            </button>
          </div>
        </form>
      )}

      {isDragActive && (
        <div className="bg-card/95 absolute inset-0 flex items-center justify-center rounded-xl text-sm font-medium">
          Drop the MP4 to start
        </div>
      )}

      {duplicate && (
        <DuplicateNotice
          duplicate={duplicate.job}
          file={
            duplicate.pending.kind === "file" ? duplicate.pending.file : null
          }
          onAgain={addAgain}
          onRerankWithFile={(file) => {
            const id = duplicate.job.id;
            setDuplicate(null);
            void startUpload(file, id);
          }}
          onClose={() => setDuplicate(null)}
        />
      )}

      <p
        id="new-job-hint"
        className={cn(
          "px-3 pt-1 pb-1.5 text-xs",
          duplicate && "sr-only",
          invalid ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {invalid
          ? "That doesn't look like a YouTube link. Paste the video's full address."
          : "English podcasts work best. You can also drop an MP4 here."}
      </p>
    </div>
  );
}
