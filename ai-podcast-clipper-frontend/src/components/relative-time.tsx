import { useNow } from "~/lib/use-now";
import { relativeTime } from "~/lib/format";

/** "5 min ago", kept fresh by the shared clock (every 30 s, so it never goes stale). */
export function RelativeTime({ iso }: { iso: string }) {
  const now = useNow();
  return (
    <time dateTime={iso} suppressHydrationWarning>
      {relativeTime(iso, now)}
    </time>
  );
}
