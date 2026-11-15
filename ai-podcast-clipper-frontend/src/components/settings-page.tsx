"use client";

import { Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { UsdInr } from "~/lib/job-types";
import {
  type AppSettings,
  DEFAULT_SETTINGS,
  MAX_CLIPS_RANGE,
  REPLAY_WEIGHT_MAX,
} from "~/lib/settings-types";
import { cn } from "~/lib/utils";
import { Segmented } from "./ui/segmented";

type SaveState = "saved" | "saving" | "error";

export function SettingsPage({
  initialSettings,
  rate,
}: {
  initialSettings: AppSettings;
  rate: UsdInr | null;
}) {
  const [settings, setSettings] = useState(initialSettings);
  const [state, setState] = useState<SaveState>("saved");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<Partial<AppSettings>>({});

  // Saves shortly after the last change (the slider fires many changes).
  const update = (change: Partial<AppSettings>) => {
    setSettings((s) => ({ ...s, ...change }));
    pending.current = { ...pending.current, ...change };
    setState("saving");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(), 400);
  };

  const save = async () => {
    const change = pending.current;
    pending.current = {};
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
      if (!response.ok) throw new Error(String(response.status));
      const saved = (await response.json()) as AppSettings;
      // Newer changes made while this request was in flight stay as they are.
      if (Object.keys(pending.current).length === 0) setSettings(saved);
      setState("saved");
    } catch {
      setState("error");
    }
  };

  // Leaving the page (a link, the back button, closing the tab) must not lose a change
  // made in the last 400 ms: send what is pending with keepalive, which outlives the page.
  useEffect(() => {
    const flush = () => {
      if (timer.current) clearTimeout(timer.current);
      const change = pending.current;
      if (Object.keys(change).length === 0) return;
      pending.current = {};
      void fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
        keepalive: true,
      }).catch(() => undefined);
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  const weight = Math.round(settings.replayWeight * 100);
  const rateText = rate
    ? `Today $1 = ₹${rate.rate.toFixed(2)}${rate.stale ? ` (saved on ${rate.date ?? "an earlier day"})` : ""}, rates by ${rate.source === "exchangerate-api" ? "ExchangeRate-API (exchangerate-api.com)" : "Frankfurter (frankfurter.dev)"}.`
    : "The exchange rate could not be read right now; costs show in dollars until it can.";

  return (
    <div className="mx-auto w-full max-w-2xl space-y-5 px-4 py-8 sm:px-6 sm:py-12">
      <div className="space-y-1.5">
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
          Settings
        </h1>
        <p className="text-muted-foreground text-sm">
          Changes apply to your next video or re-rank.
        </p>
      </div>

      <Group title="Display">
        <Row
          title="Currency"
          description={`Rupee costs use the dollar rate on the day each run finished. ${rateText}`}
          stackOnPhone
        >
          <Segmented
            label="Currency"
            value={settings.currency}
            options={[
              ["usd", "Dollars ($)"],
              ["inr", "Rupees (₹)"],
            ]}
            onChange={(currency) => update({ currency })}
          />
        </Row>
      </Group>

      <Group title="Choosing clips">
        <Row
          title="Clips per video"
          description="How many clips a new video or re-rank makes."
        >
          <div className="inline-flex items-center rounded-lg border">
            <button
              type="button"
              aria-label="Fewer clips"
              disabled={settings.maxClips <= MAX_CLIPS_RANGE[0]}
              onClick={() => update({ maxClips: settings.maxClips - 1 })}
              className="text-muted-foreground hover:text-foreground inline-flex size-8 items-center justify-center disabled:opacity-40"
            >
              <Minus className="size-4" aria-hidden />
            </button>
            <output
              className="tabular w-8 text-center text-sm font-semibold"
              aria-live="polite"
            >
              {settings.maxClips}
            </output>
            <button
              type="button"
              aria-label="More clips"
              disabled={settings.maxClips >= MAX_CLIPS_RANGE[1]}
              onClick={() => update({ maxClips: settings.maxClips + 1 })}
              className="text-muted-foreground hover:text-foreground inline-flex size-8 items-center justify-center disabled:opacity-40"
            >
              <Plus className="size-4" aria-hidden />
            </button>
          </div>
        </Row>
        <Row
          title="Most replayed"
          description="Use the parts YouTube viewers rewatch most as clip candidates."
        >
          <Toggle
            label="Most replayed"
            checked={settings.mostReplayed}
            onChange={(on) => update({ mostReplayed: on })}
          />
        </Row>
        <Row
          title="Audience weight"
          description={
            settings.mostReplayed
              ? `How much rewatching counts in a clip's score. At ${weight}%, the score is ${100 - weight}% Gemini and ${weight}% viewers.`
              : "Turn on Most replayed to use it."
          }
          muted={!settings.mostReplayed}
          stackOnPhone
        >
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={0}
              max={REPLAY_WEIGHT_MAX * 100}
              step={5}
              value={weight}
              disabled={!settings.mostReplayed}
              aria-label="Audience weight"
              onChange={(e) =>
                update({ replayWeight: Number(e.target.value) / 100 })
              }
              className="accent-tally w-40 disabled:opacity-40"
            />
            <span className="tabular w-10 text-right text-sm font-semibold">
              {weight}%
            </span>
          </div>
        </Row>
        <Row
          title="SponsorBlock"
          description="Keep sponsor reads, intros, outros and previews out of clips."
        >
          <Toggle
            label="SponsorBlock"
            checked={settings.sponsorBlock}
            onChange={(on) => update({ sponsorBlock: on })}
          />
        </Row>
      </Group>

      <div className="text-muted-foreground flex items-center justify-between px-1 text-sm">
        <span className="inline-flex items-center gap-2" aria-live="polite">
          <span
            aria-hidden
            className={cn(
              "inline-block size-2 rounded-full",
              state === "saved" && "bg-stage-transcribe",
              state === "saving" && "bg-tally",
              state === "error" && "bg-destructive",
            )}
          />
          {state === "saved"
            ? "Saved"
            : state === "saving"
              ? "Saving…"
              : "Could not save. Is the app still running?"}
        </span>
        <button
          type="button"
          onClick={() =>
            update({ ...DEFAULT_SETTINGS, currency: settings.currency })
          }
          className="hover:text-foreground underline underline-offset-4"
        >
          Reset clip settings
        </button>
      </div>
    </div>
  );
}

function Group({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h2 className="text-muted-foreground px-1 text-sm font-medium">
        {title}
      </h2>
      <div className="bg-card divide-y rounded-xl border px-4 sm:px-5">
        {children}
      </div>
    </section>
  );
}

function Row({
  title,
  description,
  children,
  muted,
  stackOnPhone,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  muted?: boolean;
  stackOnPhone?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex justify-between gap-4 py-4",
        stackOnPhone ? "flex-col sm:flex-row sm:items-center" : "items-center",
      )}
    >
      <div className={cn("min-w-0 space-y-0.5", muted && "opacity-60")}>
        <div className="text-sm font-medium">{title}</div>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {description}
        </p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 items-center rounded-full transition-colors",
        checked ? "bg-tally" : "bg-switch-off",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block size-[18px] rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-[19px]" : "translate-x-[3px]",
        )}
      />
    </button>
  );
}
