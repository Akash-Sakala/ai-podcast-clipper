"use client";

import { domAnimation, LazyMotion, MotionConfig } from "motion/react";

/**
 * The motion library, loaded once for the whole app: only the DOM animation
 * features (LazyMotion keeps the bundle small; components use `m.*`, not `motion.*`).
 * reducedMotion="user" drops transform animations when the system asks for reduced
 * motion and keeps the opacity fades, so state changes still read.
 */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
