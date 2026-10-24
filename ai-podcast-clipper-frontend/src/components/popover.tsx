"use client";

import { useEffect, useId, useRef, useState } from "react";
import { cn } from "~/lib/utils";

/**
 * Minimal accessible popover: a trigger button and a panel that closes on
 * outside click, Escape (focus returns to the trigger) and, for menus, on select.
 */
export function Popover({
  label,
  trigger,
  children,
  align = "end",
  className,
  triggerClassName,
  role = "dialog",
}: {
  label: string;
  trigger: React.ReactNode;
  children: (close: () => void) => React.ReactNode;
  align?: "start" | "end";
  className?: string;
  triggerClassName?: string;
  role?: "dialog" | "menu";
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => {
    setOpen(false);
    button.current?.focus();
  };

  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup={role}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "text-muted-foreground hover:text-foreground hover:bg-accent inline-flex size-9 items-center justify-center rounded-md transition-colors",
          open && "bg-accent text-foreground",
          triggerClassName,
        )}
      >
        {trigger}
      </button>
      {open && (
        <div
          id={id}
          role={role}
          aria-label={label}
          className={cn(
            "bg-popover text-popover-foreground absolute top-full z-30 mt-2 rounded-lg border p-1.5 shadow-lg shadow-black/10 dark:shadow-black/40",
            align === "end" ? "right-0" : "left-0",
            className,
          )}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}
