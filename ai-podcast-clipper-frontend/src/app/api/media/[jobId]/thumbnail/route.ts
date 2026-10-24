import fsp from "node:fs/promises";
import { isValidJobId, readJob, thumbnailPath } from "~/lib/jobs";
import { youtubeVideoId } from "~/lib/youtube";

export const dynamic = "force-dynamic";

/**
 * The video's cover for the list. Uploads get a frame saved by runner.py; for
 * YouTube jobs the cover is fetched from YouTube once and kept in the job folder.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  if (!isValidJobId(jobId)) return new Response("Not found", { status: 404 });
  const file = thumbnailPath(jobId);

  let image: Uint8Array | null = null;
  try {
    image = await fsp.readFile(file);
  } catch {
    const job = await readJob(jobId);
    const id = youtubeVideoId(job?.youtubeUrl);
    if (id) image = await fetchYoutubeCover(id, file);
  }
  if (!image) return new Response("Not found", { status: 404 });

  return new Response(new Uint8Array(image), {
    headers: {
      "Content-Type": "image/jpeg",
      "Cache-Control": "private, max-age=86400",
    },
  });
}

async function fetchYoutubeCover(id: string, file: string) {
  try {
    // mqdefault: 320x180, always present and already 16:9 (no letterboxing).
    const response = await fetch(`https://i.ytimg.com/vi/${id}/mqdefault.jpg`, {
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return null;
    const image = new Uint8Array(await response.arrayBuffer());
    const tmp = `${file}.tmp`;
    await fsp.writeFile(tmp, image);
    await fsp.rename(tmp, file);
    return image;
  } catch {
    return null; // offline: the list shows a placeholder and tries again later
  }
}
