"use client";

import { Link2, Loader2, Upload, X } from "lucide-react";
import { cn } from "~/lib/utils";
import { DuplicateNotice } from "./duplicate-notice";
import { UploadProgress } from "./upload-progress";
import { useAddVideo } from "./use-add-video";
import { useVideoDropzone } from "./use-video-dropzone";

export function NewJobBar({ onCreated }: { onCreated: () => Promise<void> }) {
  const {
    url,
    setUrl,
    invalid,
    setInvalid,
    submitting,
    checking,
    upload,
    duplicate,
    busy,
    checkFile,
    submitUrl,
    addAgain,
    rerankWithFile,
    dismissDuplicate,
  } = useAddVideo(onCreated);

  const { getRootProps, getInputProps, isDragActive, open } = useVideoDropzone({
    onFile: (file) => void checkFile(file),
    disabled: busy,
  });

  return (
    <div
      {...getRootProps()}
      className={cn(
        "bg-card relative rounded-xl border p-2 transition-colors",
        // The link box has no outline of its own: the whole bar shows the focus ring.
        "has-[input[type=url]:focus-visible]:ring-ring has-[input[type=url]:focus-visible]:ring-2",
        isDragActive && "border-tally ring-tally/30 ring-4",
      )}
    >
      <input {...getInputProps()} aria-label="Upload an MP4 file" />

      {upload ? (
        <UploadProgress name={upload.name} percent={upload.percent} />
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
              className="placeholder:text-muted-foreground h-11 w-full rounded-lg bg-transparent pr-9 pl-9 text-base outline-hidden sm:text-sm"
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
          onRerankWithFile={rerankWithFile}
          onClose={dismissDuplicate}
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
