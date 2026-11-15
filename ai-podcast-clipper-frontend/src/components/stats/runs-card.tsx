import Link from "next/link";
import { plural, stepTime, timecode, type Money } from "~/lib/format";
import { RUNS_PAGE, type RunRow, type Stats } from "~/lib/stats-types";
import { cn } from "~/lib/utils";
import { Card } from "../ui/card";
import { RunBar } from "./run-bar";

function shortDate(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const FAILED_AT: Record<string, string> = {
  audio: "audio upload",
  downloading: "download",
  uploading: "upload",
  clips: "clip upload",
  transcribing: "transcription",
  ranking: "ranking",
  rendering: "render",
  saving: "saving",
};

function result(run: RunRow) {
  if (run.status === "failed")
    return run.failedStage && FAILED_AT[run.failedStage]
      ? `Failed at ${FAILED_AT[run.failedStage]}`
      : "Failed";
  if (run.status === "no-clips" || !run.clips) return "No clips";
  return plural(run.clips, "clip");
}

export function RunsCard({
  stats,
  m,
  onShowMore,
}: {
  stats: Stats;
  m: (v: Money | null) => string;
  onShowMore: () => void;
}) {
  // The server sends the newest runs asked for; "Show more" asks for RUNS_PAGE more.
  const rows = stats.runs;
  const max = stats.runMaxSeconds;
  const remaining = stats.runsTotal - rows.length;
  const title = (run: RunRow) =>
    run.deleted ? (
      <span
        className="text-muted-foreground block truncate"
        title={`${run.title} (deleted)`}
      >
        {run.title}
      </span>
    ) : (
      <Link
        href={`/videos/${run.jobId}`}
        className="block truncate underline-offset-4 hover:underline"
        title={run.title}
      >
        {run.title}
      </Link>
    );
  return (
    <Card
      title="Runs"
      description="Newest first. Hover or tap a time bar for each stage's time and cost."
    >
      {/* Wide screens: a table */}
      <table className="hidden w-full table-fixed text-sm sm:table">
        <thead>
          <tr className="text-muted-foreground border-b text-left">
            <th className="w-36 py-2 pr-3 font-normal">Date</th>
            <th className="py-2 pr-3 font-normal">Video</th>
            <th className="w-20 py-2 pr-3 font-normal">Run</th>
            <th className="hidden w-20 py-2 pr-3 text-right font-normal md:table-cell">
              Length
            </th>
            <th className="w-32 py-2 pr-3 font-normal">Result</th>
            <th className="w-56 py-2 pr-3 font-normal">Time</th>
            <th className="w-20 py-2 text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((run) => (
            <tr key={run.key} className="border-b">
              <td className="py-2.5 pr-3 whitespace-nowrap">
                <time dateTime={run.startedAt} suppressHydrationWarning>
                  {shortDate(run.startedAt)}
                </time>
              </td>
              <td className="max-w-0 py-2.5 pr-3">{title(run)}</td>
              <td className="text-muted-foreground py-2.5 pr-3">
                {run.mode === "new" ? "New" : "Re-rank"}
              </td>
              <td className="tabular hidden py-2.5 pr-3 text-right md:table-cell">
                {run.videoSeconds ? timecode(run.videoSeconds) : "—"}
              </td>
              <td
                className={cn(
                  "py-2.5 pr-3 whitespace-nowrap",
                  run.status === "failed" && "text-destructive",
                )}
              >
                {result(run)}
              </td>
              <td className="tabular py-2.5 pr-3 whitespace-nowrap">
                <span className="inline-flex items-center gap-3">
                  <RunBar run={run} max={max} m={m} />
                  {run.seconds !== null ? stepTime(run.seconds) : "—"}
                </span>
              </td>
              <td className="tabular py-2.5 text-right whitespace-nowrap">
                {m(run.cost)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Phones: one block per run */}
      <ul className="divide-y sm:hidden">
        {rows.map((run) => (
          <li key={run.key} className="space-y-1.5 py-3 first:pt-0">
            <div className="text-sm font-medium">{title(run)}</div>
            <div className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
              <time dateTime={run.startedAt} suppressHydrationWarning>
                {shortDate(run.startedAt)}
              </time>
              <span>{run.mode === "new" ? "New" : "Re-rank"}</span>
              <span
                className={cn(run.status === "failed" && "text-destructive")}
              >
                {result(run)}
              </span>
            </div>
            <div className="tabular flex items-center justify-between gap-3 text-sm">
              <span className="inline-flex items-center gap-3">
                <RunBar run={run} max={max} m={m} />
                {run.seconds !== null ? stepTime(run.seconds) : "—"}
              </span>
              <span>{m(run.cost)}</span>
            </div>
          </li>
        ))}
      </ul>

      {remaining > 0 && (
        <button
          type="button"
          onClick={onShowMore}
          className="text-muted-foreground hover:text-foreground mt-3 text-sm underline-offset-4 hover:underline"
        >
          Show {Math.min(RUNS_PAGE, remaining)} more
        </button>
      )}
      {stats.reconcile && (
        <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
          This month these runs add up to {m(stats.reconcile.runs)} of the{" "}
          {m(stats.reconcile.apps)} Modal charged for running the app.
        </p>
      )}
    </Card>
  );
}
