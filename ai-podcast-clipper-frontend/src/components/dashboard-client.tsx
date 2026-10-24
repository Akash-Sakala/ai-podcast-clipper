"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { isActive, type Job } from "~/lib/job-types";
import { NewJobBar } from "./new-job-bar";
import { VideoList } from "./video-list";
import { displayTitle, useJobActions } from "./video-parts";

const POLL_INTERVAL_MS = 3000;

export function DashboardClient({ initialJobs }: { initialJobs: Job[] }) {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>(initialJobs);

  const refreshJobs = useCallback(async () => {
    try {
      const response = await fetch("/api/jobs", { cache: "no-store" });
      if (response.ok) setJobs((await response.json()) as Job[]);
    } catch {
      // The dev server may be restarting; the next poll tries again.
    }
  }, []);

  const anyActive = jobs.some(isActive);
  useEffect(() => {
    if (!anyActive) return;
    const timer = setInterval(() => void refreshJobs(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [anyActive, refreshJobs]);

  // Announce videos that finish while this page is open.
  const [previous, setPrevious] = useState(jobs);
  if (previous !== jobs) {
    for (const job of jobs) {
      const before = previous.find((p) => p.id === job.id);
      if (!before || !isActive(before) || isActive(job)) continue;
      const open = {
        label: "View clips",
        onClick: () => router.push(`/videos/${job.id}`),
      };
      if (job.status === "done" && job.clips.length > 0) {
        toast.success(`${job.clips.length} clips ready`, {
          description: displayTitle(job),
          action: open,
        });
      } else if (job.status === "failed") {
        toast.error("A video failed", {
          description: displayTitle(job),
          action: open,
        });
      }
    }
    setPrevious(jobs);
  }

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
            Paste a YouTube link or drop an MP4 above. Each video is
            transcribed, its strongest moments are ranked, and every pick comes
            back as a vertical clip with subtitles.
          </div>
        ) : (
          <VideoList jobs={jobs} busy={busy} onRetry={retry} />
        )}
      </section>
    </div>
  );
}
