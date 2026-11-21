// The app's settings (Settings page), shared by server and client code.
// Saved in <DATA_DIR>/settings.json; runner.py reads the same file before each run
// (backend settings.apply_user_settings), and its values win over the backend .env.

export type Currency = "usd" | "inr";

export type AppSettings = {
  currency: Currency;
  /** Clips per new video or re-rank. */
  maxClips: number;
  /** Use YouTube's Most replayed graph. */
  mostReplayed: boolean;
  /** Keep SponsorBlock-marked parts out of clips. */
  sponsorBlock: boolean;
  /** Share of a clip's score from the audience, 0-0.6. */
  replayWeight: number;
  /** A browser notification when a run ends while the app's tab is in the background. */
  notifyWhenDone: boolean;
};

export const DEFAULT_SETTINGS: AppSettings = {
  currency: "usd",
  maxClips: 5,
  mostReplayed: true,
  sponsorBlock: true,
  replayWeight: 0.4,
  notifyWhenDone: false,
};

export const MAX_CLIPS_RANGE = [3, 8] as const;
export const REPLAY_WEIGHT_MAX = 0.6;

/** Keeps known keys with valid values; anything else falls back to `base`. */
export function sanitizeSettings(
  raw: unknown,
  base: AppSettings = DEFAULT_SETTINGS,
): AppSettings {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  const out = { ...base };
  if (value.currency === "usd" || value.currency === "inr")
    out.currency = value.currency;
  if (typeof value.maxClips === "number" && Number.isInteger(value.maxClips))
    out.maxClips = Math.min(
      MAX_CLIPS_RANGE[1],
      Math.max(MAX_CLIPS_RANGE[0], value.maxClips),
    );
  if (typeof value.mostReplayed === "boolean")
    out.mostReplayed = value.mostReplayed;
  if (typeof value.sponsorBlock === "boolean")
    out.sponsorBlock = value.sponsorBlock;
  if (
    typeof value.replayWeight === "number" &&
    Number.isFinite(value.replayWeight)
  )
    out.replayWeight =
      Math.round(
        Math.min(REPLAY_WEIGHT_MAX, Math.max(0, value.replayWeight)) * 100,
      ) / 100;
  if (typeof value.notifyWhenDone === "boolean")
    out.notifyWhenDone = value.notifyWhenDone;
  return out;
}
