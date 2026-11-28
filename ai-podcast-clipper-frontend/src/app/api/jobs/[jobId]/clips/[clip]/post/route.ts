import { NextResponse } from "next/server";
import { readJob, writeJob } from "~/lib/job-store";
import { isActive } from "~/lib/job-types";
import { isValidClipName, isValidJobId } from "~/lib/paths";
import { applyPostChange } from "~/lib/post-text";
import { isCancelling } from "~/lib/stale-jobs";

/**
 * Changes to one clip's post text and posted marks, one job at a time: autosaves from
 * the page can overlap, and each must start from the job.json the last one wrote.
 */
const queues = new Map<string, Promise<unknown>>();
function oneAtATime<T>(jobId: string, task: () => Promise<T>): Promise<T> {
  const previous = queues.get(jobId) ?? Promise.resolve();
  const next = previous.then(task, task);
  queues.set(
    jobId,
    next.catch(() => undefined),
  );
  return next;
}

/**
 * Save a clip's post text (title, caption, hashtags, credit) or its posted marks
 * (job.json clipDetails[].post). Only while the video is not running: the runner
 * rewrites clipDetails during a run.
 */
export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ jobId: string; clip: string }> },
) {
  const { jobId, clip } = await params;
  const name = `${clip.replace(/\.mp4$/, "")}.mp4`;
  if (!isValidJobId(jobId) || !isValidClipName(name)) {
    return NextResponse.json({ error: "Invalid clip" }, { status: 400 });
  }
  let change: unknown;
  try {
    change = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  return oneAtATime(jobId, async () => {
    const job = await readJob(jobId);
    if (!job) {
      return NextResponse.json({ error: "Job not found" }, { status: 404 });
    }
    if (isActive(job) || isCancelling(jobId)) {
      return NextResponse.json(
        {
          error:
            "This video is running. Its text can be changed once it is done.",
        },
        { status: 409 },
      );
    }
    const details = job.clipDetails ?? [];
    const index = details.findIndex((d) => d.name === name);
    if (index < 0) {
      return NextResponse.json({ error: "Clip not found" }, { status: 404 });
    }
    const post = applyPostChange(details[index]!.post, change);
    if (!post) {
      return NextResponse.json({ error: "Nothing to save" }, { status: 400 });
    }
    const clipDetails = details.map((d, i) =>
      i === index ? { ...d, post } : d,
    );
    await writeJob({
      ...job,
      clipDetails,
      updatedAt: new Date().toISOString(),
    });
    return NextResponse.json({ post });
  });
}
