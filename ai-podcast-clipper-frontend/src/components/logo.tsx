import { cn } from "~/lib/utils";

/** The grey bars outside the frame and the amber bars inside it: [x, y1, y2]. */
const OUTER = [
  [3, 14, 18],
  [6.5, 11.5, 20.5],
  [25.5, 12, 20],
  [29, 14.5, 17.5],
] as const;
const INNER = [
  [13, 12.5, 19.5],
  [16, 9, 23],
  [19, 11.5, 20.5],
] as const;

/**
 * The mark: a sound wave with the part inside a vertical 9:16 frame in amber,
 * i.e. a short clip cut from a long recording. Grey bars follow the text colour.
 * `running`: the bars move like a level meter (a video is being processed); the only
 * looping animation in the app; it stays still under reduced motion.
 */
export function LogoMark({
  className,
  running = false,
}: {
  className?: string;
  running?: boolean;
}) {
  const bar = (
    [x, y1, y2]: readonly [number, number, number],
    index: number,
  ) => (
    <path
      key={x}
      d={`M${x} ${y1}V${y2}`}
      className={cn(running && "logo-bar")}
      style={running ? { animationDelay: `${index * -0.17}s` } : undefined}
    />
  );
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      strokeLinecap="round"
      aria-hidden
      className={className}
    >
      <g stroke="currentColor" strokeOpacity="0.5" strokeWidth="2.25">
        {OUTER.map((b, i) => bar(b, [0, 3, 5, 1][i]!))}
      </g>
      <rect
        x="10"
        y="4.5"
        width="12"
        height="23"
        rx="3.5"
        stroke="var(--tally)"
        strokeWidth="2"
      />
      <g stroke="var(--tally)" strokeWidth="2.25">
        {INNER.map((b, i) => bar(b, [2, 6, 4][i]!))}
      </g>
    </svg>
  );
}
