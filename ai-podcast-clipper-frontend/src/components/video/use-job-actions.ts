import { useCallback, useState } from "react";
import { toast } from "sonner";
import { fingerprintFile } from "~/lib/fingerprint";
import { displayTitle } from "~/lib/format";
import type { Job } from "~/lib/job-types";
import { pickVideoFile, uploadVideo } from "~/lib/upload";

/** What a re-rank or retry needs of a job: a JobSummary is enough for a retry. */
type ActionJob = Pick<Job, "id" | "title"> &
  Partial<Pick<Job, "rerankNeedsFile" | "fingerprint">>;

/** Re-rank and Try again, with toasts. `onStarted` refreshes the caller's data. */
export function useJobActions(onStarted: () => Promise<void>) {
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (job: ActionJob, action: "rerank" | "retry") => {
      // An upload whose video is no longer kept: re-rank asks for the file again.
      let file: File | null = null;
      if (action === "rerank" && job.rerankNeedsFile) {
        file = await pickVideoFile();
        if (!file) return;
        const print = await fingerprintFile(file).catch(() => null);
        if (job.fingerprint && print && print !== job.fingerprint) {
          toast.error("That's a different video", {
            description: "Choose the MP4 these clips were made from.",
          });
          return;
        }
      }
      setBusy(job.id);
      const toastId = file
        ? toast.loading("Uploading the video", { description: "0%" })
        : undefined;
      try {
        if (file) {
          await uploadVideo(`/api/jobs/${job.id}/rerank`, file, (percent) =>
            toast.loading("Uploading the video", {
              id: toastId,
              description: `${percent}%`,
            }),
          );
        } else {
          const response = await fetch(`/api/jobs/${job.id}/${action}`, {
            method: "POST",
          });
          if (!response.ok) {
            const body = (await response.json().catch(() => ({}))) as {
              error?: string;
            };
            throw new Error(
              body.error ?? `Request failed (${response.status})`,
            );
          }
        }
        toast.success(action === "rerank" ? "Re-ranking" : "Trying again", {
          id: toastId,
          description:
            action === "rerank"
              ? "Reusing the transcript. The current clips stay until the new ones are ready."
              : displayTitle(job),
        });
        await onStarted();
      } catch (error) {
        toast.error(
          action === "rerank" ? "Couldn't re-rank" : "Couldn't try again",
          { id: toastId, description: (error as Error).message },
        );
      } finally {
        setBusy(null);
      }
    },
    [onStarted],
  );

  return {
    busy,
    rerank: (job: ActionJob) => void run(job, "rerank"),
    retry: (job: ActionJob) => void run(job, "retry"),
  };
}
