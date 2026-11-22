"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { displayTitle } from "~/lib/format";
import { isActive, type JobSummary } from "~/lib/job-types";
import { jobsChanged, useJobs } from "~/lib/job-sync";
import { NewJobBar } from "./new-job/new-job-bar";
import { VideoList } from "./video-list";
import { useJobActions } from "./video/use-job-actions";

export function DashboardClient({
  initialJobs,
  servedAt,
}: {
  initialJobs: JobSummary[];
  /** Server time the list was read at (see lib/job-sync.ts). */
  servedAt: number;
}) {
  const router = useRouter();
  // The tab's live list (lib/job-sync.ts): kept fresh on every page, so coming back
  // here (also with Back) shows what is on disk now, not this page's first copy.
  const jobs = useJobs(initialJobs, servedAt);

  // Announce videos that finish while this page is open. The state each job had when
  // the page first showed it is the starting point, so a video that finished while
  // another page was open is not announced on arrival.
  const wasActive = useRef<Map<string, boolean> | null>(null);
  useEffect(() => {
    if (!wasActive.current) {
      wasActive.current = new Map(jobs.map((job) => [job.id, isActive(job)]));
      return;
    }
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

  const { busy, retry } = useJobActions();

  return (
    <div className="mx-auto w-full max-w-4xl space-y-10 px-4 py-8 sm:px-6 sm:py-12">
      <div className="space-y-4">
        <h1 className="font-display text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
          Find the moments worth posting.
        </h1>
        <NewJobBar onCreated={jobsChanged} />
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
