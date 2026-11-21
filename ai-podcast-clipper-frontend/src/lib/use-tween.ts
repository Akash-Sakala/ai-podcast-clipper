"use client";

import { animate, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { ease } from "./motion";

/** How long a number takes to glide to its new value. */
const TWEEN_SECONDS = 0.45;

/**
 * Numbers that glide to new values when `key` changes (the stats period switching from
 * This month to All time), and change silently otherwise (a background refresh). The
 * first render shows the real values at once: they arrive in the page's HTML.
 */
export function useTween(values: number[], key: string): number[] {
  const [shown, setShown] = useState(values);
  const shownRef = useRef(values);
  shownRef.current = shown;
  const latest = useRef({ key, values });
  const reduce = useReducedMotion();
  const signature = values.join(",");

  useEffect(() => {
    const before = latest.current;
    latest.current = { key, values: signature.split(",").map(Number) };
    const target = latest.current.values;
    const from = shownRef.current;
    if (before.key === key || reduce || from.length !== target.length) {
      setShown(target);
      return;
    }
    const controls = animate(0, 1, {
      duration: TWEEN_SECONDS,
      ease: ease.move,
      onUpdate: (t) =>
        setShown(from.map((start, i) => start + (target[i]! - start) * t)),
    });
    return () => controls.stop();
  }, [key, signature, reduce]);

  return shown;
}
