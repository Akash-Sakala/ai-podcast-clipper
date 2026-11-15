import { stepTime } from "~/lib/format";
import { Card } from "../ui/card";

export function TrendCard({
  points,
}: {
  points: { startedAt: string; seconds: number }[];
}) {
  const W = 1000;
  const H = 180;
  const pad = { l: 44, r: 12, t: 12, b: 24 };
  const times = points.map((p) => new Date(p.startedAt).getTime());
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  // Whole-minute gridlines: every minute up to 6 min, else every 2 or 5 minutes.
  const longest = Math.max(...points.map((p) => p.seconds), 60);
  const step = longest <= 360 ? 60 : longest <= 900 ? 120 : 300;
  const yMax = Math.ceil((longest * 1.05) / step) * step;
  const x = (t: number) =>
    pad.l +
    (t1 > t0
      ? ((t - t0) / (t1 - t0)) * (W - pad.l - pad.r)
      : (W - pad.l - pad.r) / 2);
  const y = (s: number) => H - pad.b - (s / yMax) * (H - pad.t - pad.b);
  const ticks = Array.from({ length: yMax / step + 1 }, (_, i) => i * step);
  const date = (t: number) =>
    new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return (
    <Card
      title="Time per new video"
      description="Each dot is one new video, oldest on the left."
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-44 w-full"
        role="img"
        aria-label="Processing time of each new video over time"
      >
        {ticks.map((s) => (
          <g key={s}>
            <line
              x1={pad.l}
              x2={W - pad.r}
              y1={y(s)}
              y2={y(s)}
              className="stroke-border"
              strokeWidth={1}
            />
            <text
              x={pad.l - 6}
              y={y(s) + 4}
              textAnchor="end"
              className="fill-muted-foreground text-[11px]"
            >
              {s ? `${s / 60} min` : "0"}
            </text>
          </g>
        ))}
        <text x={pad.l} y={H - 6} className="fill-muted-foreground text-[11px]">
          {date(t0)}
        </text>
        <text
          x={W - pad.r}
          y={H - 6}
          textAnchor="end"
          className="fill-muted-foreground text-[11px]"
        >
          {date(t1)}
        </text>
        {points.map((p) => (
          <circle
            key={p.startedAt}
            cx={x(new Date(p.startedAt).getTime())}
            cy={y(p.seconds)}
            r={5}
            className="fill-stage-download stroke-card"
            strokeWidth={2}
          >
            <title>{`${date(new Date(p.startedAt).getTime())}: ${stepTime(p.seconds)}`}</title>
          </circle>
        ))}
      </svg>
    </Card>
  );
}
