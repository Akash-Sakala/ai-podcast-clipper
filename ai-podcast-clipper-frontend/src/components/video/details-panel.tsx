import { duration, formatMoney, plural } from "~/lib/format";
import { isActive, type Job } from "~/lib/job-types";
import { runTotalUsd } from "~/lib/stats-math";
import type { Currency } from "~/lib/settings-types";

export function DetailsPanel({
  job,
  currency = "usd",
  rate = null,
}: {
  job: Job;
  currency?: Currency;
  /** Today's rate, for a run that has no rate of its own. */
  rate?: number | null;
}) {
  const rows: { label: string; value: React.ReactNode }[] = [];
  rows.push({
    label: "Added",
    value: new Date(job.createdAt).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }),
  });
  rows.push({
    label: "Source",
    value: job.youtubeUrl ? (
      <a
        href={job.youtubeUrl}
        target="_blank"
        rel="noreferrer"
        className="underline-offset-4 hover:underline"
      >
        Open on YouTube
      </a>
    ) : (
      "Uploaded video"
    ),
  });
  const audio = job.engine?.parakeet?.audioSeconds;
  if (audio) rows.push({ label: "Video length", value: duration(audio) });
  const speed = job.engine?.parakeet?.realTimeFactor;
  rows.push({
    label: "Transcription",
    value: `Parakeet${job.engine?.gpu ? ` on ${job.engine.gpu}` : ""}${speed ? `, ${Math.round(speed)}× real time` : ""}`,
  });
  const model = job.ranking?.modelUsed ?? job.ranking?.model;
  if (model) rows.push({ label: "Moments picked by", value: model });
  const replay = job.replay;
  if (job.source === "youtube" && replay && !isActive(job)) {
    const onPeak = (job.clipDetails ?? []).filter((d) => d.peak).length;
    const peaks = replay.peaks.length;
    rows.push({
      label: "Most replayed",
      value: replay.available
        ? peaks
          ? `${peaks} ${peaks === 1 ? "hotspot" : "hotspots"}, ${onPeak} ${onPeak === 1 ? "clip" : "clips"} on them`
          : "No clear hotspots"
        : replay.errors?.some((e) => e.startsWith("YouTube"))
          ? "Couldn't read it"
          : "No graph for this video yet",
    });
    const skipped = replay.sponsor?.segments.length;
    if (replay.sponsor)
      rows.push({
        label: "SponsorBlock",
        value: skipped
          ? `${skipped} ${skipped === 1 ? "part" : "parts"} left out`
          : "Nothing marked",
      });
  }
  const total = job.timings?.total;
  if (typeof total === "number" && !isActive(job))
    rows.push({ label: "Processing time", value: duration(total) });
  if (job.cost && !isActive(job)) {
    const runRate = job.runs?.at(-1)?.usdInr?.rate ?? rate;
    const money = (usd: number) =>
      formatMoney(
        { usd, inr: runRate !== null ? usd * runRate : null },
        currency,
      );
    const gemini = job.cost.gemini;
    rows.push({
      label: "Estimated cost",
      value: `${money(runTotalUsd(job.cost))}${job.cost.gpu ? ` on ${job.cost.gpu}` : ""}`,
    });
    if (gemini)
      rows.push({
        label: "Gemini",
        value: `${money(gemini.usd)} of it, ${plural(gemini.calls, "call")}`,
      });
  }
  if (job.reranks)
    rows.push({
      label: "Re-ranked",
      value: job.reranks === 1 ? "Once" : `${job.reranks} times`,
    });

  return (
    <dl className="grid w-80 grid-cols-[auto_1fr] gap-x-4 gap-y-2 p-2 text-sm">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 text-right break-words">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
