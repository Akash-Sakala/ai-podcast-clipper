"use client";

import { LazyMotion, MotionConfig } from "motion/react";

// All features (layout animations included) arrive in their own chunk after the page
// loads, so the first paint never waits for them; components use `m.*`, not `motion.*`.
const loadFeatures = () =>
  import("motion/react").then((library) => library.domMax);

/**
 * The motion library, loaded once for the whole app. reducedMotion="user" drops
 * transform and layout animations when the system asks for reduced motion and keeps the
 * opacity fades, so state changes still read.
 */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
