"use client";

import { Check, Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState } from "react";
import { cn } from "~/lib/utils";
import { Popover } from "./popover";

const OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

export function ThemeMenu() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // The icon turns in only when the theme changes after the page loaded, not on load.
  const shownTheme = useRef(resolvedTheme);
  const [turns, setTurns] = useState(0);
  useEffect(() => {
    if (shownTheme.current && shownTheme.current !== resolvedTheme)
      setTurns((n) => n + 1);
    shownTheme.current = resolvedTheme;
  }, [resolvedTheme]);

  // Before hydration the theme is unknown; render a neutral icon to avoid a mismatch.
  const Icon = !mounted ? Monitor : resolvedTheme === "dark" ? Moon : Sun;

  return (
    <Popover
      label="Theme"
      role="menu"
      trigger={
        <Icon
          key={turns}
          className={cn("size-[18px]", turns > 0 && "icon-swap")}
          aria-hidden
        />
      }
      className="w-40"
    >
      {(close) =>
        OPTIONS.map((option) => {
          const selected = mounted && theme === option.value;
          return (
            <button
              key={option.value}
              type="button"
              role="menuitemradio"
              aria-checked={selected}
              onClick={() => {
                setTheme(option.value);
                close();
              }}
              className="hover:bg-accent press flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm pointer-coarse:py-3"
            >
              <option.icon
                className="text-muted-foreground size-4"
                aria-hidden
              />
              <span className="flex-1 text-left">{option.label}</span>
              {selected && <Check className="size-4" aria-hidden />}
            </button>
          );
        })
      }
    </Popover>
  );
}
