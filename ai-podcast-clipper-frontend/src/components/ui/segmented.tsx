"use client";

import { m } from "motion/react";
import { useId, useRef } from "react";
import { duration, ease } from "~/lib/motion";
import { cn } from "~/lib/utils";

/**
 * A two-or-more way choice drawn as a pill, with the radio-group keyboard pattern:
 * one tab stop (the selected option), arrow keys move the choice, Home/End jump.
 * The white pill slides to the chosen option (a shared layout animation).
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly (readonly [T, string])[];
  onChange: (value: T) => void;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const thumb = useId();

  const move = (to: number) => {
    const index = (to + options.length) % options.length;
    const next = options[index];
    if (!next) return;
    onChange(next[0]);
    buttons.current[index]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        move(index + 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        move(index - 1);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(options.length - 1);
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="bg-muted inline-flex rounded-lg p-0.5 text-sm"
    >
      {options.map(([option, text], index) => (
        <button
          key={option}
          ref={(el) => {
            buttons.current[index] = el;
          }}
          type="button"
          role="radio"
          aria-checked={value === option}
          tabIndex={value === option ? 0 : -1}
          onClick={() => onChange(option)}
          onKeyDown={(event) => onKeyDown(event, index)}
          className={cn(
            "press relative rounded-md px-3 py-1 whitespace-nowrap pointer-coarse:py-2.5",
            value === option
              ? "text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {value === option && (
            <m.span
              layoutId={thumb}
              aria-hidden
              className="bg-card absolute inset-0 rounded-md shadow-sm"
              transition={{ duration: duration.enter, ease: ease.move }}
            />
          )}
          <span className="relative">{text}</span>
        </button>
      ))}
    </div>
  );
}
