"use client";

import { m } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipDetail, ProgressStage } from "~/lib/job-types";
import { duration, ease } from "~/lib/motion";
import { ClipCard } from "../clip-card";
import { PendingClip } from "./pending-clip";

type Item = { name: string; rank: number; plan: ClipDetail | null };

/**
 * The clips of a running first run: one place per clip from the moment the ranking is
 * known (plannedClips), each showing its rank, title and what is happening to it, and
 * turning into the playable clip when it is saved. Clips saved while the page is open
 * fade in and their frame lights up once; screen readers hear "Clip 2 ready: <title>".
 * Older runners don't publish plannedClips: then the places have no titles.
 */
export function LiveClips({
  jobId,
  saved,
  planned,
  total,
  stage,
  versionOf,
  replayWeight,
}: {
  jobId: string;
  saved: ClipDetail[];
  planned: ClipDetail[] | null;
  /** How many clips are being rendered (progress.total), for runs without plannedClips. */
  total: number;
  stage: ProgressStage | "queued";
  versionOf: (clip: string) => string;
  replayWeight?: number;
}) {
  const savedByName = useMemo(
    () => new Map(saved.map((d) => [d.name, d])),
    [saved],
  );
  // Clips already saved when the page opened don't light up; later ones do.
  const [initial] = useState(() => new Set(saved.map((d) => d.name)));

  const items: Item[] = useMemo(() => {
    if (planned?.length) {
      const names = new Set(planned.map((p) => p.name));
      return [
        ...planned.map((p) => ({ name: p.name, rank: p.rank, plan: p })),
        ...saved
          .filter((d) => !names.has(d.name))
          .map((d) => ({ name: d.name, rank: d.rank, plan: d })),
      ].sort((a, b) => a.rank - b.rank);
    }
    const waiting = Math.max(0, total - saved.length);
    return [
      ...saved.map((d) => ({ name: d.name, rank: d.rank, plan: d })),
      ...Array.from({ length: waiting }, (_, i) => ({
        name: `pending-${i}`,
        rank: saved.length + i + 1,
        plan: null,
      })),
    ];
  }, [planned, saved, total]);

  const [announcement, setAnnouncement] = useState("");
  const announced = useRef(new Set(initial));
  useEffect(() => {
    for (const d of saved) {
      if (announced.current.has(d.name)) continue;
      announced.current.add(d.name);
      setAnnouncement(`Clip ${d.rank} ready${d.title ? `: ${d.title}` : ""}.`);
    }
  }, [saved]);

  const state =
    stage === "rendering"
      ? "Rendering"
      : stage === "saving"
        ? "Saving"
        : "Up next";

  return (
    <>
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((item) => {
          const detail = savedByName.get(item.name);
          const fresh = !!detail && !initial.has(item.name);
          return (
            <m.div
              key={item.name}
              layout="position"
              transition={{ duration: duration.emphasis, ease: ease.move }}
            >
              {detail ? (
                <m.div
                  initial={fresh ? { opacity: 0, scale: 0.97 } : false}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: duration.enter, ease: ease.enter }}
                >
                  <ClipCard
                    jobId={jobId}
                    clip={item.name}
                    version={versionOf(item.name)}
                    detail={detail}
                    replayWeight={replayWeight}
                    arrived={fresh}
                  />
                </m.div>
              ) : (
                <PendingClip
                  detail={item.plan}
                  rank={item.rank}
                  state={state}
                />
              )}
            </m.div>
          );
        })}
      </div>
    </>
  );
}
