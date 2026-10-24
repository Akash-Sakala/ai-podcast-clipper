import { NextResponse } from "next/server";
import { findDuplicate } from "~/lib/jobs";

export const dynamic = "force-dynamic";

/**
 * Has this video been added before? Body: `{ "youtubeUrl": "..." }` or
 * `{ "fingerprint": "<sha-256 hex>" }` (see lib/fingerprint.ts).
 * Answers `{ "duplicate": null }` or a short summary of the newest matching job.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as {
    youtubeUrl?: string;
    fingerprint?: string;
  } | null;
  const fingerprint = /^[0-9a-f]{64}$/.test(body?.fingerprint ?? "")
    ? body?.fingerprint
    : undefined;
  const job = await findDuplicate({
    youtubeUrl: body?.youtubeUrl?.trim(),
    fingerprint,
  });
  return NextResponse.json({
    duplicate: job
      ? {
          id: job.id,
          title: job.title,
          status: job.status,
          clips: job.clips.length,
          canRerank: !!job.canRerank,
          hasThumbnail: !!job.hasThumbnail,
          createdAt: job.createdAt,
        }
      : null,
  });
}
