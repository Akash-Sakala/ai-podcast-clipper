import { Loader2 } from "lucide-react";

export function UploadProgress({
  name,
  percent,
}: {
  name: string;
  percent: number;
}) {
  return (
    <div className="flex h-11 items-center gap-3 px-2">
      <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3 text-sm">
          <span className="truncate font-medium">{name}</span>
          <span className="text-muted-foreground tabular shrink-0">
            {percent < 100 ? `${percent}%` : "Starting"}
          </span>
        </div>
        <div className="bg-muted mt-1.5 h-1 overflow-hidden rounded-full">
          <span
            className="bg-tally block h-full transition-[width]"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
    </div>
  );
}
