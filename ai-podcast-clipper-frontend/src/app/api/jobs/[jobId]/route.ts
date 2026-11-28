import os from "node:os";
import fsp from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { jsonWithEtag } from "~/lib/http";
import { isActive } from "~/lib/job-types";
import { getJob } from "~/lib/jobs";
import { jobDir, JOBS_DIR } from "~/lib/paths";
import { isCancelling } from "~/lib/stale-jobs";
import { removeKeptVideo } from "~/lib/video-store";

/** One job. Its page reads this (lib/job-sync.ts), every 3 s while it runs (If-None-Match: 304 when unchanged). */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const servedAt = Date.now();
  const job = await getJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  return jsonWithEtag(request, job, servedAt);
}

/**
 * Delete a video: its job folder (clips, transcript, job.json), the copy kept for
 * re-ranks and a download left in the temp folder. Its runs stay in the stats ledger.
 * A running video must be cancelled first.
 */
export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const job = await getJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  if (isActive(job) || isCancelling(jobId)) {
    return NextResponse.json(
      { error: "This video is still running. Cancel it first." },
      { status: 409 },
    );
  }
  // Moved aside first, in one step: Windows refuses while a clip is open (a player, or a
  // stream this server is ending), and then nothing is deleted at all. Folders named
  // like this are not job ids, so the list never shows them.
  const trash = path.join(JOBS_DIR, `.deleted-${jobId}-${Date.now()}`);
  try {
    await renameWithRetries(jobDir(jobId), trash);
  } catch {
    return NextResponse.json(
      {
        error:
          "Couldn't delete it because one of its files is open. Close any open clip and try again.",
      },
      { status: 423 },
    );
  }
  await fsp
    .rm(trash, {
      recursive: true,
      force: true,
      maxRetries: 8,
      retryDelay: 250,
    })
    .catch(() => undefined);
  // Folders an earlier delete could not finish (a file was still open then).
  for (const name of await fsp.readdir(JOBS_DIR).catch(() => [] as string[])) {
    if (name.startsWith(".deleted-") && !trash.endsWith(name)) {
      await fsp
        .rm(path.join(JOBS_DIR, name), { recursive: true, force: true })
        .catch(() => undefined);
    }
  }
  await removeKeptVideo(jobId);
  await fsp
    .rm(path.join(os.tmpdir(), "ai-podcast-clipper", jobId), {
      recursive: true,
      force: true,
    })
    .catch(() => undefined);
  return NextResponse.json({ ok: true });
}

async function renameWithRetries(from: string, to: string) {
  for (let attempt = 1; ; attempt++) {
    try {
      await fsp.rename(from, to);
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code ?? "";
      if (!["EPERM", "EBUSY", "EACCES"].includes(code) || attempt >= 8) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
    }
  }
}
