"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "~/lib/utils";

const MARGIN = 8;
const GAP = 6;
const LONG_PRESS_MS = 500;
const TOUCH_HIDE_MS = 1500;

/** Only one tooltip is open at a time: opening one hides the last. */
let hideCurrent: (() => void) | null = null;

function hoverDelay() {
  const value = getComputedStyle(document.documentElement).getPropertyValue(
    "--tooltip-delay",
  );
  return Number.parseFloat(value) || 400;
}

/**
 * A short label for an icon-only control. It shows after a short wait under a mouse,
 * at once on keyboard focus, and on a long press on a touch screen (the press then
 * doesn't click). Escape, scrolling, a click or leaving hides it. The control keeps
 * its own aria-label (the same words), so the tooltip itself is hidden from screen
 * readers. It is placed above the control, or below when there's no room, and
 * always inside the viewport.
 */
export function Tooltip({
  label,
  children,
  media,
  disabled = false,
  className,
}: {
  label: string;
  children: React.ReactNode;
  /** Show only while this media query matches (a label hidden on small screens). */
  media?: string;
  disabled?: boolean;
  className?: string;
}) {
  const wrap = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const touching = useRef(false);
  const longPressed = useRef(false);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  const hide = useCallback(() => {
    window.clearTimeout(timer.current);
    setOpen(false);
    setPos(null);
  }, []);

  const show = useCallback(() => {
    if (disabled) return;
    if (media && !window.matchMedia(media).matches) return;
    if (hideCurrent && hideCurrent !== hide) hideCurrent();
    hideCurrent = hide;
    setOpen(true);
  }, [disabled, media, hide]);

  useEffect(() => {
    if (disabled) hide();
  }, [disabled, hide]);

  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
      if (hideCurrent === hide) hideCurrent = null;
    },
    [hide],
  );

  // Placed before paint: centred on the control, kept inside the viewport.
  useLayoutEffect(() => {
    if (!open || !wrap.current || !tip.current) return;
    const anchor = wrap.current.getBoundingClientRect();
    const width = tip.current.offsetWidth;
    const height = tip.current.offsetHeight;
    const viewport = document.documentElement.clientWidth;
    const centred = anchor.left + anchor.width / 2 - width / 2;
    const left = Math.max(MARGIN, Math.min(centred, viewport - MARGIN - width));
    const above = anchor.top - GAP - height;
    setPos({ left, top: above >= MARGIN ? above : anchor.bottom + GAP });
  }, [open, label]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") hide();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [open, hide]);

  return (
    <span
      ref={wrap}
      className={cn("inline-flex [-webkit-touch-callout:none]", className)}
      onPointerEnter={(event) => {
        if (event.pointerType !== "mouse") return;
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(show, hoverDelay());
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "mouse") hide();
      }}
      onPointerDown={(event) => {
        if (event.pointerType === "mouse") return hide();
        touching.current = true;
        longPressed.current = false;
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          longPressed.current = true;
          show();
        }, LONG_PRESS_MS);
      }}
      onPointerUp={() => {
        if (!touching.current) return;
        touching.current = false;
        window.clearTimeout(timer.current);
        if (longPressed.current)
          timer.current = window.setTimeout(hide, TOUCH_HIDE_MS);
      }}
      onPointerCancel={() => {
        touching.current = false;
        hide();
      }}
      // A long press showed the label: the release must not also press the control.
      onClickCapture={(event) => {
        if (!longPressed.current) return;
        longPressed.current = false;
        event.preventDefault();
        event.stopPropagation();
      }}
      onContextMenu={(event) => {
        if (touching.current || longPressed.current) event.preventDefault();
      }}
      onFocus={(event) => {
        if ((event.target as Element).matches(":focus-visible")) show();
      }}
      onBlur={hide}
    >
      {children}
      {open &&
        createPortal(
          <span
            ref={tip}
            aria-hidden
            className="bg-foreground text-background pop-in pointer-events-none fixed z-50 rounded-md px-2 py-1 text-xs font-medium whitespace-nowrap shadow-md"
            style={
              pos
                ? { left: pos.left, top: pos.top }
                : { left: 0, top: 0, visibility: "hidden" }
            }
          >
            {label}
          </span>,
          document.body,
        )}
    </span>
  );
}
