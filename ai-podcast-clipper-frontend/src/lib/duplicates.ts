import { createHash } from "node:crypto";
import fsp from "node:fs/promises";
import path from "node:path";
import { fingerprintRanges } from "./fingerprint";
import { ACTIVE_STATUSES, type Job } from "./job-types";
import { updateJob } from "./job-store";
import { listJobs } from "./jobs";
import { jobDir } from "./paths";
import { youtubeVideoId } from "./youtube";

/** Server: the same fingerprint as fingerprintFile() in the browser, for a file on disk. */
export async function fileFingerprint(file: string): Promise<string | null> {
  let handle: fsp.FileHandle | null = null;
  try {
    handle = await fsp.open(file, "r");
    const { size } = await handle.stat();
    const hash = createHash("sha256").update(`${size}:`);
    for (const [start, end] of fingerprintRanges(size)) {
      const buffer = Buffer.alloc(end - start);
      await handle.read(buffer, 0, buffer.length, start);
      hash.update(buffer);
    }
    return hash.digest("hex");
  } catch {
    return null;
  } finally {
    await handle?.close();
  }
}

/**
 * The newest job for the same video: the same YouTube video id, or an upload with
 * the same content fingerprint. Older uploads get their fingerprint computed (and
 * saved) the first time they are compared.
 */
export async function findDuplicate(query: {
  youtubeUrl?: string;
  fingerprint?: string;
}): Promise<Job | null> {
  const videoId = youtubeVideoId(query.youtubeUrl);
  if (!videoId && !query.fingerprint) return null;
  for (const job of await listJobs()) {
    if (videoId && youtubeVideoId(job.youtubeUrl) === videoId) return job;
    if (query.fingerprint && job.source === "upload") {
      let print = job.fingerprint;
      if (!print) {
        print =
          (await fileFingerprint(path.join(jobDir(job.id), "input.mp4"))) ??
          undefined;
        // Only idle jobs are written to; the runner owns job.json while it works.
        if (print && !ACTIVE_STATUSES.includes(job.status)) {
          await updateJob(job.id, { fingerprint: print });
        }
      }
      if (print === query.fingerprint) return job;
    }
  }
  return null;
}
