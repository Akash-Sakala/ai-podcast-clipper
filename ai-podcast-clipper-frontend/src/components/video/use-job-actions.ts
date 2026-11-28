import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { toast } from "sonner";
import { fingerprintFile } from "~/lib/fingerprint";
import { displayTitle } from "~/lib/format";
import { jobsChanged } from "~/lib/job-sync";
import type { Job } from "~/lib/job-types";
import { pickVideoFile, uploadVideo } from "~/lib/upload";

/** What a re-rank or retry needs of a job: a JobSummary is enough for a retry. */
type ActionJob = Pick<Job, "id" | "title"> &
  Partial<Pick<Job, "rerankNeedsFile" | "fingerprint">>;

async function request(url: string, method: "POST" | "DELETE") {
  const response = await fetch(url, { method });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: string;
    };
    throw new Error(body.error ?? `Request failed (${response.status})`);
  }
}

/**
 * Re-rank, Try again, Cancel and Delete, with toasts. Afterwards every tab reads the
 * job again (jobsChanged).
 */
export function useJobActions() {
  const [busy, setBusy] = useState<string | null>(null);
  const router = useRouter();

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
          await request(`/api/jobs/${job.id}/${action}`, "POST");
        }
        toast.success(action === "rerank" ? "Re-ranking" : "Trying again", {
          id: toastId,
          description:
            action === "rerank"
              ? "Reusing the transcript. The current clips stay until the new ones are ready."
              : displayTitle(job),
        });
        await jobsChanged(job.id);
      } catch (error) {
        toast.error(
          action === "rerank" ? "Couldn't re-rank" : "Couldn't try again",
          { id: toastId, description: (error as Error).message },
        );
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  /** Stops a running video; resolves once it is stopped (true) or could not be (false). */
  const cancel = useCallback(async (job: ActionJob) => {
    setBusy(job.id);
    try {
      await request(`/api/jobs/${job.id}/cancel`, "POST");
      toast.success("Cancelled", {
        description: "Try again picks up where it stopped.",
      });
      await jobsChanged(job.id);
      return true;
    } catch (error) {
      toast.error("Couldn't cancel", {
        description: (error as Error).message,
      });
      return false;
    } finally {
      setBusy(null);
    }
  }, []);

  /** Deletes a video and goes back to the list; resolves false when it couldn't. */
  const remove = useCallback(
    async (job: ActionJob) => {
      setBusy(job.id);
      try {
        await request(`/api/jobs/${job.id}`, "DELETE");
        toast.success("Video deleted", { description: displayTitle(job) });
        router.push("/");
        await jobsChanged();
        return true;
      } catch (error) {
        toast.error("Couldn't delete it", {
          description: (error as Error).message,
        });
        return false;
      } finally {
        setBusy(null);
      }
    },
    [router],
  );

  return {
    busy,
    rerank: (job: ActionJob) => void run(job, "rerank"),
    retry: (job: ActionJob) => void run(job, "retry"),
    cancel,
    remove,
  };
}
