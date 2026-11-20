"use client";

import { useState } from "react";
import type { ClipComparison, ClipDetail, ClipSet } from "~/lib/job-types";

/** One clip of either set, as the selection and the API name it. */
export type ClipPick = { set: ClipSet; clip: string };

export const pickKey = (set: ClipSet, clip: string) => `${set}/${clip}`;

/**
 * Two clips are the same moment when their time ranges overlap by at least half of the
 * shorter one.
 */
export function sameMoment(a?: ClipDetail, b?: ClipDetail) {
  if (a?.start == null || a.end == null || b?.start == null || b.end == null)
    return false;
  const shorter = Math.min(a.end - a.start, b.end - b.start);
  if (shorter <= 0) return false;
  const overlap = Math.min(a.end, b.end) - Math.max(a.start, b.start);
  return overlap >= shorter / 2;
}

/** For each new clip, the previous clip that is the same moment (most overlap first). */
export function matchesOf(newDetails: ClipDetail[], previous: ClipDetail[]) {
  const matches = new Map<string, ClipDetail>();
  for (const detail of newDetails) {
    const overlap = (other: ClipDetail) =>
      Math.min(detail.end ?? 0, other.end ?? 0) -
      Math.max(detail.start ?? 0, other.start ?? 0);
    const same = previous
      .filter((other) => sameMoment(detail, other))
      .sort((x, y) => overlap(y) - overlap(x));
    if (same[0]) matches.set(detail.name, same[0]);
  }
  return matches;
}

/** When the comparison opens: every new clip, no previous one (so a moment in both sets
 *  is only ticked once, in the new set). */
export function initialPicks(comparison: ClipComparison) {
  return new Set(comparison.new.clips.map((clip) => pickKey("new", clip)));
}

/**
 * The ticked clips. Kept across polls; set up again only when another comparison opens
 * (a different `createdAt`).
 */
export function useComparisonSelection(
  comparison: ClipComparison | null | undefined,
) {
  const key = comparison?.createdAt ?? null;
  const [state, setState] = useState(() => ({
    key,
    picked: comparison ? initialPicks(comparison) : new Set<string>(),
  }));
  let current = state;
  if (state.key !== key) {
    current = {
      key,
      picked: comparison ? initialPicks(comparison) : new Set<string>(),
    };
    setState(current); // adjusting state while rendering: React re-renders at once
  }

  const toggle = (set: ClipSet, clip: string, keep: boolean) =>
    setState((s) => {
      const picked = new Set(s.picked);
      if (keep) picked.add(pickKey(set, clip));
      else picked.delete(pickKey(set, clip));
      return { ...s, picked };
    });
  const pickAll = (set: ClipSet, clips: string[]) =>
    setState((s) => ({
      ...s,
      picked: new Set(clips.map((clip) => pickKey(set, clip))),
    }));

  return { picked: current.picked, toggle, pickAll };
}
