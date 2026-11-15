import fsp from "node:fs/promises";
import path from "node:path";
import { DATA_DIR } from "./paths";
import {
  type AppSettings,
  DEFAULT_SETTINGS,
  sanitizeSettings,
} from "./settings-types";

/** The Settings page's file. runner.py reads it before every run. */
export const SETTINGS_FILE = path.join(DATA_DIR, "settings.json");

export async function readSettings(): Promise<AppSettings> {
  try {
    return sanitizeSettings(
      JSON.parse(await fsp.readFile(SETTINGS_FILE, "utf8")),
    );
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

/** Merges a partial update into the saved settings and writes them atomically. */
export async function saveSettings(update: unknown): Promise<AppSettings> {
  const current = await readSettings();
  const next = sanitizeSettings(
    { ...current, ...(update && typeof update === "object" ? update : {}) },
    current,
  );
  await fsp.mkdir(DATA_DIR, { recursive: true });
  const tmp = `${SETTINGS_FILE}.${process.pid}.tmp`;
  await fsp.writeFile(tmp, JSON.stringify(next, null, 2));
  await fsp.rename(tmp, SETTINGS_FILE);
  return next;
}
