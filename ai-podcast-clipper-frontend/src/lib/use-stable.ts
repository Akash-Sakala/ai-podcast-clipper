"use client";

import { useRef } from "react";

/**
 * Returns the previous reference while `value` is deeply equal to it (compared as
 * JSON). A poll hands back fresh objects every time; this keeps unchanged parts
 * referentially stable so memoised children skip re-rendering.
 */
export function useStable<T>(value: T): T {
  const ref = useRef(value);
  if (
    ref.current !== value &&
    JSON.stringify(ref.current) !== JSON.stringify(value)
  ) {
    ref.current = value;
  }
  return ref.current;
}
