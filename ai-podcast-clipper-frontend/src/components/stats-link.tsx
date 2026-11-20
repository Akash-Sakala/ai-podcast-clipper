"use client";

import { ChartColumn } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { iconButtonClass } from "./ui/icon-button";
import { Tooltip } from "./ui/tooltip";

/** Header link to the stats page, styled like the theme menu's button. */
export function StatsLink() {
  const active = usePathname() === "/stats";
  return (
    <Tooltip label="Stats">
      <Link
        href="/stats"
        aria-label="Stats"
        aria-current={active ? "page" : undefined}
        className={iconButtonClass("md", active)}
      >
        <ChartColumn className="size-[18px]" aria-hidden />
      </Link>
    </Tooltip>
  );
}
