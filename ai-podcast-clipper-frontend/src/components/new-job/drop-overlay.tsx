"use client";

import { FileWarning, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import type { DropzoneRootProps } from "react-dropzone";
import { cn } from "~/lib/utils";

/**
 * True while files are dragged over the window (not text or links), so the whole page
 * can take the drop. Ends when the drag leaves the window, is dropped, or is cancelled.
 */
export function useWindowFileDrag(enabled: boolean) {
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setDragging(false);
      return;
    }
    const hasFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types ?? []).includes("Files");
    const onEnter = (event: DragEvent) => {
      if (hasFiles(event)) setDragging(true);
    };
    // relatedTarget is null when the drag leaves the window itself.
    const onLeave = (event: DragEvent) => {
      if (event.relatedTarget === null) setDragging(false);
    };
    const onEnd = () => setDragging(false);
    // The drop is heard on the way down (capture): the drop target stops it on the way up.
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onEnd, true);
    window.addEventListener("dragend", onEnd);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onEnd, true);
      window.removeEventListener("dragend", onEnd);
    };
  }, [enabled]);
  return dragging;
}

/**
 * The page while an MP4 is dragged over it: the logo's amber 9:16 frame, dashed, asking
 * for the drop. The overlay is the drop target (react-dropzone root props); a file that
 * isn't an MP4 turns the frame red before it is dropped.
 */
export function DropOverlay({
  rootProps,
  reject,
}: {
  rootProps: DropzoneRootProps;
  reject: boolean;
}) {
  return (
    <div
      {...rootProps}
      className="bg-background/85 fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm"
    >
      <div className="pop-in pointer-events-none flex flex-col items-center gap-5 text-center">
        <div
          className={cn(
            "flex aspect-[9/16] w-32 items-center justify-center rounded-2xl border-2 border-dashed sm:w-40",
            reject
              ? "border-destructive text-destructive"
              : "border-tally text-tally-ink dark:text-tally bg-tally-soft/60",
          )}
        >
          {reject ? (
            <FileWarning className="size-8" aria-hidden />
          ) : (
            <Upload className="size-8" aria-hidden />
          )}
        </div>
        <div className="space-y-1" role="status">
          <p className="font-display text-xl font-semibold">
            {reject ? "Only .mp4 files" : "Drop to upload"}
          </p>
          <p className="text-muted-foreground text-sm">
            {reject
              ? "Choose one .mp4 video up to 4 GB."
              : "One .mp4 video, up to 4 GB."}
          </p>
        </div>
      </div>
    </div>
  );
}
