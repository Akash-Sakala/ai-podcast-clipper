"use client";

import { cn } from "~/lib/utils";
import { Tooltip } from "./tooltip";

/**
 * Classes for an icon-only control: 32 px (sm) or 36 px (md) with a mouse, 44 px on a
 * touch screen (pointer: coarse), so desktop stays compact and fingers get a full
 * target. For links styled the same way (next/link), wrap them in <Tooltip> yourself.
 */
export function iconButtonClass(size: "sm" | "md" = "md", active = false) {
  return cn(
    "press text-muted-foreground hover:text-foreground hover:bg-accent inline-flex shrink-0 items-center justify-center rounded-md disabled:pointer-events-none disabled:opacity-40",
    size === "sm" ? "size-8" : "size-9",
    "pointer-coarse:size-11",
    active && "bg-accent text-foreground",
  );
}

type Common = {
  /** What the control does; becomes its accessible name and its tooltip. */
  label: string;
  /** The icon (decorative: give it aria-hidden). */
  children: React.ReactNode;
  size?: "sm" | "md";
  active?: boolean;
  className?: string;
};

type AsButton = Common & {
  href?: undefined;
  onClick?: () => void;
  disabled?: boolean;
};

type AsLink = Common & {
  href: string;
  /** File name for a download link. */
  download?: string;
};

/** An icon-only button or link with a required label and a tooltip. */
export function IconButton(props: AsButton | AsLink) {
  const { label, children, size, active, className } = props;
  const classes = cn(iconButtonClass(size, active), className);
  return (
    <Tooltip label={label}>
      {props.href !== undefined ? (
        <a
          href={props.href}
          download={props.download}
          aria-label={label}
          className={classes}
        >
          {children}
        </a>
      ) : (
        <button
          type="button"
          onClick={props.onClick}
          disabled={props.disabled}
          aria-label={label}
          className={classes}
        >
          {children}
        </button>
      )}
    </Tooltip>
  );
}
