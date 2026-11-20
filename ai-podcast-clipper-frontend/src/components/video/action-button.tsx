import { Loader2 } from "lucide-react";
import { cn } from "~/lib/utils";
import { Tooltip } from "../ui/tooltip";

/** Matches Tailwind's `sm` breakpoint: below it a compact button shows its icon only. */
const PHONE = "(max-width: 639.98px)";

export function ActionButton({
  icon,
  children,
  onClick,
  href,
  busy,
  primary,
  compact,
}: {
  icon: React.ReactNode;
  children: string;
  onClick?: () => void;
  href?: string;
  busy?: boolean;
  primary?: boolean;
  /** Icon only on a phone (the words stay for screen readers and in a tooltip). */
  compact?: boolean;
}) {
  const className = cn(
    "press inline-flex h-8 items-center justify-center gap-1.5 rounded-md whitespace-nowrap px-2.5 text-sm font-medium disabled:pointer-events-none disabled:opacity-60 pointer-coarse:h-11",
    compact && "pointer-coarse:min-w-11",
    primary
      ? "bg-foreground text-background hover:bg-foreground/85"
      : "text-muted-foreground hover:text-foreground hover:bg-accent",
  );
  const content = (
    <>
      {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
      {compact ? (
        <span className="sr-only sm:not-sr-only">{children}</span>
      ) : (
        children
      )}
    </>
  );
  const control = href ? (
    <a href={href} className={className}>
      {content}
    </a>
  ) : (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={className}
    >
      {content}
    </button>
  );
  return compact ? (
    <Tooltip label={children} media={PHONE}>
      {control}
    </Tooltip>
  ) : (
    control
  );
}
