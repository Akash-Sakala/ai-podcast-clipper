"use client";

import { Link2, Loader2, Upload, X } from "lucide-react";
import { useState } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import { cn } from "~/lib/utils";

const YOUTUBE =
  /^(https?:\/\/)?((www|m|music)\.)?(youtube\.com|youtu\.be)\/.+/i;
const MAX_BYTES = 4 * 1024 * 1024 * 1024;

function uploadFile(file: File, onProgress: (percent: number) => void) {
  // XMLHttpRequest (not fetch) so large uploads can show progress.
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.setRequestHeader("Content-Type", "video/mp4");
    xhr.setRequestHeader("x-filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      let message = `Upload failed (${xhr.status})`;
      try {
        message =
          (JSON.parse(xhr.responseText) as { error?: string }).error ?? message;
      } catch {}
      reject(new Error(message));
    };
    xhr.onerror = () =>
      reject(new Error("The connection dropped during upload"));
    xhr.send(file);
  });
}

export function NewJobBar({ onCreated }: { onCreated: () => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [upload, setUpload] = useState<{
    name: string;
    percent: number;
  } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const busy = submitting || upload !== null;

  const startUpload = async (file: File) => {
    setUpload({ name: file.name, percent: 0 });
    try {
      await uploadFile(file, (percent) =>
        setUpload({ name: file.name, percent }),
      );
      toast.success("Video added", {
        description: "Clips will appear here when they're ready.",
      });
      await onCreated();
    } catch (error) {
      toast.error("Upload failed", { description: (error as Error).message });
    } finally {
      setUpload(null);
    }
  };

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop: (accepted) => {
      if (accepted[0]) void startUpload(accepted[0]);
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

  const submitUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = url.trim();
    if (!value) return;
    if (!YOUTUBE.test(value)) {
      setInvalid(true);
      return;
    }
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
              {submitting && <Loader2 className="size-4 animate-spin" />}
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

      <p
        id="new-job-hint"
        className={cn(
          "px-3 pt-1 pb-1.5 text-xs",
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
