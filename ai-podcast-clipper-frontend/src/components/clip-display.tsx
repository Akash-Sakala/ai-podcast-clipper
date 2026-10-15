"use client";

import { Download } from "lucide-react";
import type { Job } from "~/lib/job-types";
import { Button } from "./ui/button";

function ClipCard({ jobId, clip }: { jobId: string; clip: string }) {
  const src = `/api/media/${jobId}/${clip}`;

  return (
    <div className="flex max-w-52 flex-col gap-2">
      <div className="bg-muted rounded-md">
        <video
          src={src}
          controls
          preload="metadata"
          className="h-full w-full rounded-md object-cover"
        />
      </div>
      <Button asChild variant="outline" size="sm">
        <a href={`${src}?download`}>
          <Download className="mr-1.5 h-4 w-4" />
          Download
        </a>
      </Button>
    </div>
  );
}

export function ClipDisplay({
  jobs,
  jobsDir,
}: {
  jobs: Job[];
  jobsDir: string;
}) {
  const jobsWithClips = jobs.filter((job) => job.clips.length > 0);

  if (jobsWithClips.length === 0) {
    return (
      <p className="text-muted-foreground p-4 text-center">
        No clips generated yet.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      {jobsWithClips.map((job) => (
        <section key={job.id} className="flex flex-col gap-3">
          <div>
            <h3 className="truncate font-medium">{job.title}</h3>
            <p className="text-muted-foreground text-xs">
              {new Date(job.createdAt).toLocaleString()} · {job.clips.length}{" "}
              clip{job.clips.length !== 1 ? "s" : ""} · {jobsDir}
              {jobsDir.includes("\\") ? "\\" : "/"}
              {job.id}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {job.clips.map((clip) => (
              <ClipCard key={clip} jobId={job.id} clip={clip} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
