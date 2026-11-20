import { randomBytes } from "node:crypto";
import fsp from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { ACTIVE_STATUSES, type ClipSet } from "~/lib/job-types";
import { readJob, updateJob } from "~/lib/job-store";
import { isValidClipName, isValidJobId, jobDir } from "~/lib/paths";
import { startRunner } from "~/lib/runner";

/** Where the selection goes for runner.py (--keep-clips @file, which deletes it). */
const SELECTION_FILE = "keep-clips.json";

/**
 * After a re-rank: keep the chosen clips of the new and the previous set.
 * Body: `{ keep: [{ set: "new" | "previous", clip: "clip_0.mp4" }, ...] }`.
 * The runner copies the kept clips into place file by file, best score first, and
 * deletes the rest; until then both sets stay as they are.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  if (!isValidJobId(jobId)) {
    return NextResponse.json({ error: "Invalid job id" }, { status: 400 });
  }
  const job = await readJob(jobId);
  if (!job) {
    return NextResponse.json({ error: "Job not found" }, { status: 404 });
  }
  if (ACTIVE_STATUSES.includes(job.status)) {
    return NextResponse.json(
      { error: "This job is still running" },
      { status: 409 },
    );
  }
  const comparison = job.comparison;
  if (!comparison) {
    return NextResponse.json(
      { error: "There are no new clips to choose from" },
      { status: 409 },
    );
  }

  const body = (await request.json().catch(() => null)) as {
    keep?: unknown;
  } | null;
  const keep = body?.keep;
  if (!Array.isArray(keep) || keep.length === 0) {
    return NextResponse.json(
      { error: "Choose at least one clip to keep" },
      { status: 400 },
    );
  }
  const available: Record<ClipSet, string[]> = {
    new: comparison.new.clips,
    previous: job.clips,
  };
  const seen = new Set<string>();
  const selection: { set: ClipSet; clip: string }[] = [];
  for (const item of keep as unknown[]) {
    const { set, clip } = (item ?? {}) as { set?: unknown; clip?: unknown };
    if (
      (set !== "new" && set !== "previous") ||
      typeof clip !== "string" ||
      !isValidClipName(clip) ||
      !available[set].includes(clip) ||
      seen.has(`${set}/${clip}`)
    ) {
      return NextResponse.json(
        { error: "That selection doesn't match the clips of this video" },
        { status: 400 },
      );
    }
    seen.add(`${set}/${clip}`);
    selection.push({ set, clip });
  }

  const file = path.join(jobDir(jobId), SELECTION_FILE);
  const tmp = `${file}.${randomBytes(4).toString("hex")}.tmp`;
  try {
    await fsp.writeFile(tmp, JSON.stringify(selection), "utf-8");
    await fsp.rename(tmp, file);
  } catch (error) {
    await fsp.rm(tmp, { force: true });
    return NextResponse.json(
      { error: `Couldn't save the selection: ${(error as Error).message}` },
      { status: 500 },
    );
  }

  await updateJob(jobId, {
    status: "saving",
    message: "Keeping the chosen clips",
    error: null,
    progress: null,
    queuedAt: new Date().toISOString(),
    pid: undefined, // the runner records its own
  });
  await startRunner(jobId, ["--keep-clips", `@${file}`]);
  return NextResponse.json({ ok: true }, { status: 202 });
}
