import { Loader2 } from "lucide-react";
import { cn } from "~/lib/utils";

export function ActionButton({
  icon,
  children,
  onClick,
  href,
  busy,
  primary,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
  onClick?: () => void;
  href?: string;
  busy?: boolean;
  primary?: boolean;
}) {
  const className = cn(
    "inline-flex h-8 items-center gap-1.5 rounded-md whitespace-nowrap px-2.5 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-60",
    primary
      ? "bg-foreground text-background hover:bg-foreground/85"
      : "text-muted-foreground hover:text-foreground hover:bg-accent",
  );
  const content = (
    <>
      {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </>
  );
  if (href) {
    return (
      <a href={href} className={className}>
        {content}
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={className}
    >
      {content}
    </button>
  );
}
