"use client";

import Link from "next/link";
import { useState } from "react";
import { formatMoney, type Money, plural, stepTime } from "~/lib/format";
import { type Period, RUNS_PAGE, type Stats } from "~/lib/stats-types";
import { usePolling } from "~/lib/use-polling";
import { Segmented } from "../ui/segmented";
import { ClipSourcesCard } from "./clip-sources-card";
import { RunsCard } from "./runs-card";
import { StageCard } from "./stage-card";
import { BillTile, Tile } from "./tiles";
import { TrendCard } from "./trend-card";

const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 30000;

export function StatsPage({ initialStats }: { initialStats: Stats }) {
  const [stats, setStats] = useState(initialStats);
  const [period, setPeriod] = useState<Period>(initialStats.period);
  // How many runs the table asks the server for ("Show more" adds RUNS_PAGE).
  const [runsLimit, setRunsLimit] = useState(RUNS_PAGE);

  // Every 5 s while a run or the Modal bill is in progress, else every 30 s. Nothing
  // happens in a hidden tab; showing the tab again refreshes at once.
  usePolling<Stats>({
    url: `/api/stats?period=${period}&runs=${runsLimit}`,
    intervalMs:
      stats.active || stats.billRefreshing ? ACTIVE_POLL_MS : IDLE_POLL_MS,
    onData: setStats,
  });

  const m = (value: Money | null) =>
    value ? formatMoney(value, stats.currency) : "—";

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Stats
          </h1>
          <p className="text-muted-foreground text-sm">
            What each video cost on Modal and how long it took.
          </p>
        </div>
        <Segmented
          label="Period"
          value={period}
          options={[
            ["month", "This month"],
            ["all", "All time"],
          ]}
          onChange={(next) => {
            setPeriod(next);
            setRunsLimit(RUNS_PAGE);
          }}
        />
      </div>

      <div className="grid gap-3 sm:gap-4 lg:grid-cols-3">
        <BillTile stats={stats} />
        <Tile
          label="New video, average"
          value={stats.newAvg ? m(stats.newAvg.cost) : "—"}
          second={stats.newAvg ? stepTime(stats.newAvg.seconds) : undefined}
          note={
            stats.newAvg
              ? `${stats.newAvg.perHour ? `${m(stats.newAvg.perHour)} per podcast hour, ` : ""}${sample(stats.newAvg.runs)}`
              : "No new videos yet"
          }
        />
        <Tile
          label="Re-rank, average"
          value={stats.rerankAvg ? m(stats.rerankAvg.cost) : "—"}
          second={
            stats.rerankAvg ? stepTime(stats.rerankAvg.seconds) : undefined
          }
          note={
            stats.rerankAvg
              ? capitalize(sample(stats.rerankAvg.runs))
              : "No re-ranks yet"
          }
        />
      </div>

      {stats.summary.runs === 0 ? (
        <div className="text-muted-foreground rounded-xl border border-dashed px-6 py-10 text-sm leading-relaxed">
          <p className="text-foreground mb-1 font-medium">
            No runs {stats.period === "month" ? "this month" : "yet"}
          </p>
          Paste a link on{" "}
          <Link href="/" className="underline underline-offset-4">
            Home
          </Link>{" "}
          to process your first video.
        </div>
      ) : (
        <>
          <p className="text-muted-foreground -mt-2 px-0.5 text-sm">
            {summaryLine(stats)}
          </p>
          <StageCard stats={stats} m={m} />
          {stats.trend && <TrendCard points={stats.trend} />}
          <RunsCard
            stats={stats}
            m={m}
            onShowMore={() => setRunsLimit(stats.runs.length + RUNS_PAGE)}
          />
          {stats.clipSources && <ClipSourcesCard stats={stats} />}
        </>
      )}
    </div>
  );
}

function sample(runs: number) {
  const text = `from ${runs} ${runs === 1 ? "run" : "runs"}`;
  return runs < 3 ? `early estimate ${text}` : text;
}

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function summaryLine(stats: Stats) {
  const s = stats.summary;
  const parts = [
    plural(s.videos, "video"),
    plural(s.runs, "run"),
    plural(s.clips, "clip"),
  ];
  if (s.failed) parts.push(`${plural(s.failed, "failed run")}`);
  return `${stats.periodLabel}: ${parts.join(", ")}.`;
}
