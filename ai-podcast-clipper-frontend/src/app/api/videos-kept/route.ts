import { NextResponse } from "next/server";
import { keptVideosSummary, removeKeptVideos } from "~/lib/video-store";

export const dynamic = "force-dynamic";

/** The videos kept on this laptop for re-ranks: {known, count, bytes, keepDays, maxGb}. */
export async function GET() {
  return NextResponse.json(await keptVideosSummary());
}

/** Removes every kept video except running jobs'; returns {removed, bytes}. */
export async function DELETE() {
  return NextResponse.json(await removeKeptVideos());
}
