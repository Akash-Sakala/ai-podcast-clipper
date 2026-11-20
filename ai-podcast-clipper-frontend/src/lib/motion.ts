import type { Transition, Variants } from "motion/react";

/**
 * Motion tokens for the motion library, mirroring the --duration-* / --curve-* /
 * --motion-* custom properties in src/styles/globals.css (seconds here, ms there).
 * Change both together. Reduced motion is handled once, in MotionProvider.
 */
export const duration = {
  press: 0.1,
  exit: 0.13,
  enter: 0.2,
  emphasis: 0.32,
} as const;

export const ease = {
  /** Arriving: fast start, soft landing. */
  enter: [0.16, 1, 0.3, 1],
  /** Leaving: gentle start, quick finish. */
  exit: [0.55, 0, 1, 0.45],
  /** Moving between two resting places. */
  move: [0.4, 0, 0.2, 1],
} as const satisfies Record<string, Transition["ease"]>;

/** A panel opening from its trigger (popovers, menus). Fades fully on exit. */
export const popIn: Variants = {
  hidden: { opacity: 0, scale: 0.96, y: -4 },
  shown: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { duration: duration.enter, ease: ease.enter },
  },
  exit: {
    opacity: 0,
    scale: 0.98,
    y: -2,
    transition: { duration: duration.exit, ease: ease.exit },
  },
};

/** Content arriving in place (a row, a notice). */
export const fadeRise: Variants = {
  hidden: { opacity: 0, y: 8 },
  shown: {
    opacity: 1,
    y: 0,
    transition: { duration: duration.enter, ease: ease.enter },
  },
  exit: {
    opacity: 0,
    transition: { duration: duration.exit, ease: ease.exit },
  },
};

/**
 * Delay for item `index` of a list whose items use fadeRise: 35 ms between items,
 * and only the first 8 wait (items further down arrive with the 8th, so a long list
 * never feels slow). Pass it as `transition={{ delay: staggerDelay(i) }}`.
 */
export function staggerDelay(index: number) {
  return Math.min(index, 8) * 0.035;
}
