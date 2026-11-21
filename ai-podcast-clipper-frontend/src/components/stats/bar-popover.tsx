"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { cn } from "~/lib/utils";

/** Only one bar's details are open at a time: opening one closes the last. */
let closeOpen: (() => void) | null = null;

/**
 * A chart bar that opens a small table of its parts. It opens under a mouse, on keyboard
 * focus and on a tap (a tap or click keeps it open until a second one, a press outside
 * or Escape), never on hover alone. The bar is a button whose name says the whole
 * breakdown in words, so the table is extra, not the only way to read it.
 */
export function BarPopover({
  label,
  bar,
  children,
  className,
  buttonClassName,
  style,
}: {
  /** The breakdown in words, read by screen readers (the button's name). */
  label: string;
  /** The bar itself. */
  bar: React.ReactNode;
  /** The table shown above the bar. */
  children: React.ReactNode;
  className?: string;
  buttonClassName?: string;
  style?: React.CSSProperties;
}) {
  const [open, setOpen] = useState(false);
  const pinned = useRef(false);
  const root = useRef<HTMLSpanElement>(null);
  const tipId = useId();

  const close = useCallback(() => {
    pinned.current = false;
    setOpen(false);
  }, []);
  const show = useCallback(() => {
    if (closeOpen && closeOpen !== close) closeOpen();
    closeOpen = close;
    setOpen(true);
  }, [close]);

  useEffect(
    () => () => {
      if (closeOpen === close) closeOpen = null;
    },
    [close],
  );

  useEffect(() => {
    if (!open) return;
    const onPress = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPress);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPress);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  const mouse = (event: React.PointerEvent) => event.pointerType === "mouse";

  return (
    <span
      ref={root}
      className={cn("relative inline-flex", className)}
      style={style}
      onPointerEnter={(e) => mouse(e) && show()}
      onPointerLeave={(e) => mouse(e) && !pinned.current && close()}
    >
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        onFocus={(e) => {
          if (e.currentTarget.matches(":focus-visible")) show();
        }}
        onBlur={(e) => {
          if (!root.current?.contains(e.relatedTarget as Node | null)) close();
        }}
        onClick={() => {
          if (open && pinned.current) return close();
          pinned.current = true;
          show();
        }}
        className={cn("flex w-full rounded-sm text-left", buttonClassName)}
      >
        {bar}
      </button>
      {open && (
        <span
          id={tipId}
          role="tooltip"
          className="bg-popover text-popover-foreground pop-in pointer-events-none absolute bottom-full left-0 z-20 mb-1.5 origin-bottom-left rounded-md border px-3 py-2 text-xs shadow-md"
        >
          {children}
        </span>
      )}
    </span>
  );
}
