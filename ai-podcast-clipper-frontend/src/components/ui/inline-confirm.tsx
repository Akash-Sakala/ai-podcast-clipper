"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { cn } from "~/lib/utils";

/**
 * A question with a destructive button and a safe one, shown in place of the control that
 * asked it (like Settings' "Remove kept videos"). Focus starts on the safe button, so
 * Enter never confirms by accident; Escape answers it too.
 */
export function InlineConfirm({
  question,
  confirmLabel,
  keepLabel,
  onConfirm,
  onKeep,
  busy,
  className,
}: {
  question: string;
  confirmLabel: string;
  keepLabel: string;
  onConfirm: () => void;
  onKeep: () => void;
  busy?: boolean;
  className?: string;
}) {
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => keep.current?.focus(), []);
  return (
    <div
      role="group"
      aria-label={question}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onKeep();
      }}
      className={cn("pop-in flex flex-wrap items-center gap-2", className)}
    >
      <span className="text-sm">{question}</span>
      <button
        type="button"
        onClick={onConfirm}
        disabled={busy}
        // Page-background ink on red: 4.7:1 light, 5.7:1 dark (white is 3.3:1 in dark).
        className="bg-destructive text-background press inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium disabled:opacity-60 pointer-coarse:h-11"
      >
        {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
        {confirmLabel}
      </button>
      <button
        ref={keep}
        type="button"
        onClick={onKeep}
        disabled={busy}
        className="text-foreground/80 hover:text-foreground hover:bg-muted press inline-flex h-8 items-center rounded-lg border px-3 text-sm font-medium disabled:opacity-60 pointer-coarse:h-11"
      >
        {keepLabel}
      </button>
    </div>
  );
}
