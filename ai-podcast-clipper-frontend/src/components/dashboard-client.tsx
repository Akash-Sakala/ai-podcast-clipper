"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { displayTitle } from "~/lib/format";
import { isActive, type JobSummary } from "~/lib/job-types";
import { useRunReport } from "~/lib/run-signal";
import { usePolling } from "~/lib/use-polling";
import { NewJobBar } from "./new-job/new-job-bar";
import { VideoList } from "./video-list";
import { useJobActions } from "./video/use-job-actions";

const POLL_INTERVAL_MS = 3000;
const HIDDEN_POLL_INTERVAL_MS = 15_000;

export function DashboardClient({
  initialJobs,
}: {
  initialJobs: JobSummary[];
}) {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobSummary[]>(initialJobs);

  // Polls while a video is being processed (3 s); an idle page only refreshes when the
  // tab is shown again. A 304 or a hidden tab costs nothing.
  const anyActive = jobs.some(isActive);
  const refreshJobs = usePolling<JobSummary[]>({
    url: "/api/jobs",
    intervalMs: anyActive ? POLL_INTERVAL_MS : null,
    // A background tab still hears about a run ending (tab title, notification).
    hiddenIntervalMs: anyActive ? HIDDEN_POLL_INTERVAL_MS : null,
    onData: setJobs,
  });
  // The header logo, the tab title and the "done" notification follow these jobs.
  useRunReport(jobs);

  // Announce videos that finish while this page is open. The last known state of each
  // job is kept in a ref, and the toast fires from an effect (never during render).
  const wasActive = useRef(
    new Map(initialJobs.map((job) => [job.id, isActive(job)])),
  );
  useEffect(() => {
    for (const job of jobs) {
      const before = wasActive.current.get(job.id);
      wasActive.current.set(job.id, isActive(job));
      if (before !== true || isActive(job)) continue;
      const open = {
        label: "View clips",
        onClick: () => router.push(`/videos/${job.id}`),
      };
      if (job.status === "done" && job.clipCount > 0) {
        toast.success(`${job.clipCount} clips ready`, {
          id: `finished-${job.id}`,
          description: displayTitle(job),
          action: open,
        });
      } else if (job.status === "failed") {
        toast.error("A video failed", {
          id: `finished-${job.id}`,
          description: displayTitle(job),
          action: open,
        });
      }
    }
  }, [jobs, router]);

  const { busy, retry } = useJobActions(refreshJobs);

  return (
    <div className="mx-auto w-full max-w-4xl space-y-10 px-4 py-8 sm:px-6 sm:py-12">
      <div className="space-y-4">
        <h1 className="font-display text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
          Find the moments worth posting.
        </h1>
        <NewJobBar onCreated={refreshJobs} />
      </div>

      <section aria-labelledby="videos-heading" className="space-y-3">
        <h2
          id="videos-heading"
          className="text-muted-foreground text-sm font-medium"
        >
          Your videos
        </h2>
        {jobs.length === 0 ? (
          <div className="text-muted-foreground rounded-xl border border-dashed px-6 py-10 text-sm leading-relaxed">
            <p className="text-foreground mb-1 font-medium">No videos yet</p>
            Paste a YouTube link in the box above, or drop an MP4 anywhere on
            this page. Each video is transcribed, its strongest moments are
            ranked, and every pick comes back as a vertical clip with subtitles.
          </div>
        ) : (
          <VideoList jobs={jobs} busy={busy} onRetry={retry} />
        )}
      </section>
    </div>
  );
}
