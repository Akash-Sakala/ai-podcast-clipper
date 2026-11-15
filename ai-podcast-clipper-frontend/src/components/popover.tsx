"use client";

import { useEffect, useId, useRef, useState } from "react";
import { cn } from "~/lib/utils";

/**
 * Minimal accessible popover: a trigger button and a panel that closes on
 * outside click, Escape (focus returns to the trigger), when focus moves out of it
 * and, for menus, on select. A menu also follows the ARIA menu keyboard pattern:
 * focus moves to the checked item on open, arrow keys / Home / End move between
 * items, Tab closes it.
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
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();

  const items = () =>
    Array.from(
      panel.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [],
    );

  // Opening a menu from the keyboard or a pointer puts focus on its checked item.
  useEffect(() => {
    if (!open || role !== "menu") return;
    const all = items();
    (
      all.find((el) => el.getAttribute("aria-checked") === "true") ?? all[0]
    )?.focus();
  }, [open, role]);

  const onMenuKeyDown = (event: React.KeyboardEvent) => {
    if (role !== "menu") return;
    const all = items();
    const at = all.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = (at + 1) % all.length;
    else if (event.key === "ArrowUp") next = (at - 1 + all.length) % all.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = all.length - 1;
    else if (event.key === "Tab") setOpen(false);
    if (next !== null) {
      event.preventDefault();
      all[next]?.focus();
    }
  };

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
    <div
      ref={root}
      className="relative"
      onBlur={(event) => {
        // Focus moved to something outside (a null target is a click in Safari: ignore).
        const to = event.relatedTarget as Node | null;
        if (open && to && !root.current?.contains(to)) setOpen(false);
      }}
    >
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
          ref={panel}
          id={id}
          role={role}
          onKeyDown={onMenuKeyDown}
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
