"use client";

import { Link2, Loader2, Upload, X, Youtube } from "lucide-react";
import { AnimatePresence, m } from "motion/react";
import { useEffect, useRef } from "react";
import { duration, ease } from "~/lib/motion";
import { cn } from "~/lib/utils";
import { parseYoutubeLink } from "~/lib/youtube";
import { DropOverlay, useWindowFileDrag } from "./drop-overlay";
import { DuplicateNotice } from "./duplicate-notice";
import { UploadProgress } from "./upload-progress";
import { useAddVideo } from "./use-add-video";
import { useVideoDropzone } from "./use-video-dropzone";

/** Something the user is typing into, where a paste must stay theirs. */
function isEditable(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return (
    !!el &&
    (el.isContentEditable ||
      el.tagName === "INPUT" ||
      el.tagName === "TEXTAREA")
  );
}

export function NewJobBar({ onCreated }: { onCreated: () => Promise<void> }) {
  const {
    url,
    setUrl,
    linkState,
    lookingUp,
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
  const input = useRef<HTMLInputElement>(null);

  const { getRootProps, getInputProps, isDragReject, open } = useVideoDropzone({
    onFile: (file) => void checkFile(file),
    disabled: busy,
  });
  const dragging = useWindowFileDrag(!busy);

  // Ctrl+V anywhere on the page (outside another field): a YouTube link fills the box,
  // an MP4 copied in the file explorer starts like a dropped one.
  const latest = useRef({ busy, checkFile, setUrl, setInvalid });
  latest.current = { busy, checkFile, setUrl, setInvalid };
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const { busy, checkFile, setUrl, setInvalid } = latest.current;
      if (busy || isEditable(event.target)) return;
      const file = event.clipboardData?.files?.[0];
      if (file && /\.mp4$/i.test(file.name)) {
        event.preventDefault();
        void checkFile(file);
        return;
      }
      const text = event.clipboardData?.getData("text")?.trim() ?? "";
      if (!parseYoutubeLink(text)) return;
      event.preventDefault();
      setUrl(text);
      setInvalid(false);
      input.current?.focus();
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);

  const ready = linkState === "valid" && !duplicate;
  const hint = invalid
    ? "That doesn't look like a YouTube link. Paste the video's full address."
    : lookingUp || checking
      ? "Checking whether you added this video before."
      : ready
        ? "Ready to make clips from this YouTube video."
        : "Paste a YouTube link, or drop an MP4 anywhere on this page. English podcasts work best.";

  return (
    <div
      className={cn(
        "bg-card relative rounded-xl border p-2 transition-[box-shadow,border-color] duration-(--motion-enter)",
        // The link box has no outline of its own: the whole bar shows the focus ring.
        "has-[input[type=url]:focus-visible]:ring-ring has-[input[type=url]:focus-visible]:ring-2",
        ready && "border-tally/70",
      )}
    >
      <input {...getInputProps()} aria-label="Upload an MP4 file" />

      {upload ? (
        <UploadProgress upload={upload} />
      ) : (
        <form onSubmit={submitUrl} className="flex flex-col gap-2 sm:flex-row">
          <label className="relative flex min-w-0 flex-1 items-center">
            <span className="sr-only">YouTube link</span>
            {linkState === "valid" ? (
              <Youtube
                key="youtube"
                className="icon-swap text-tally-ink dark:text-tally pointer-events-none absolute left-3 size-4"
                aria-hidden
              />
            ) : (
              <Link2
                key="link"
                className="text-muted-foreground pointer-events-none absolute left-3 size-4"
                aria-hidden
              />
            )}
            <input
              ref={input}
              type="url"
              inputMode="url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setInvalid(false);
              }}
              onPaste={(e) => {
                // A pasted address that isn't YouTube is flagged at once, not on submit.
                const text = e.clipboardData.getData("text").trim();
                if (text && !parseYoutubeLink(text))
                  window.setTimeout(() => setInvalid(true));
              }}
              onBlur={() => {
                if (linkState === "invalid") setInvalid(true);
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
                  input.current?.focus();
                }}
                aria-label="Clear link"
                className="text-muted-foreground hover:text-foreground press absolute right-2 inline-flex size-7 items-center justify-center rounded-md pointer-coarse:right-0 pointer-coarse:size-11"
              >
                <X className="size-4" aria-hidden />
              </button>
            )}
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={open}
              disabled={busy}
              className="text-muted-foreground hover:text-foreground hover:bg-accent press inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium disabled:opacity-60 sm:flex-none"
            >
              <Upload className="size-4" aria-hidden />
              Upload MP4
            </button>
            <button
              type="submit"
              disabled={busy}
              onClick={(event) => {
                // Nothing to add yet: the button points at the link box instead.
                if (linkState === "empty") {
                  event.preventDefault();
                  input.current?.focus();
                }
              }}
              className={cn(
                "press inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold sm:flex-none",
                ready || busy
                  ? "bg-tally text-tally-ink hover:bg-tally/90"
                  : "bg-tally-soft text-foreground hover:bg-tally/25",
              )}
            >
              {(submitting || checking) && (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              )}
              {submitting ? "Adding" : checking ? "Checking" : "Make clips"}
            </button>
          </div>
        </form>
      )}

      <AnimatePresence initial={false}>
        {duplicate && (
          <m.div
            key="duplicate"
            initial={{ height: 0, opacity: 0 }}
            animate={{
              height: "auto",
              opacity: 1,
              transition: { duration: duration.enter, ease: ease.enter },
            }}
            exit={{
              height: 0,
              opacity: 0,
              transition: { duration: duration.exit, ease: ease.exit },
            }}
            className="overflow-hidden"
          >
            <DuplicateNotice
              duplicate={duplicate.job}
              file={
                duplicate.pending.kind === "file"
                  ? duplicate.pending.file
                  : null
              }
              onAgain={addAgain}
              onRerankWithFile={rerankWithFile}
              onClose={dismissDuplicate}
            />
          </m.div>
        )}
      </AnimatePresence>

      {!upload && (
        <p
          id="new-job-hint"
          role={invalid ? "alert" : undefined}
          className={cn(
            "px-3 pt-1 pb-1.5 text-xs",
            duplicate && "sr-only",
            invalid ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {hint}
        </p>
      )}

      {dragging && (
        <DropOverlay rootProps={getRootProps()} reject={isDragReject} />
      )}
    </div>
  );
}
