import { AlertCircle } from "lucide-react";
import { cn } from "~/lib/utils";

export function Notice({
  tone = "neutral",
  children,
  action,
}: {
  tone?: "neutral" | "error";
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg px-3.5 py-3 text-sm sm:flex-row sm:items-center",
        tone === "error"
          ? "bg-destructive/10 text-foreground"
          : "bg-tally-soft text-foreground",
      )}
    >
      <AlertCircle
        className={cn(
          "hidden size-4 shrink-0 sm:block",
          tone === "error"
            ? "text-destructive"
            : "text-tally-ink dark:text-tally",
        )}
      />
      <p className="flex-1 leading-relaxed">{children}</p>
      {action && <div className="flex shrink-0 gap-1">{action}</div>}
    </div>
  );
}
