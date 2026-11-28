"use client";

import { Check, Copy, RotateCw } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import type { ClipDetail, Platform } from "~/lib/job-types";
import { jobsChanged } from "~/lib/job-sync";
import {
  cleanHashtags,
  draftOf,
  PLATFORMS,
  POST_LIMITS,
  postAll,
  type PostChange,
  type PostDraft,
} from "~/lib/post-text";
import { cn } from "~/lib/utils";

/** Typing stops for this long before the text is saved. */
const SAVE_DELAY_MS = 800;
/** How long "Saved" stays after a save. */
const SAVED_SHOWN_MS = 2000;

type SaveState = "idle" | "saving" | "saved" | "error";
type Pending = { clip: string; change: PostChange };

/**
 * A clip's post text and posted marks while the viewer shows it. One draft serves both
 * places the viewer draws its details (beside the clip, and the phone's sheet). Text is
 * saved a moment after typing stops, a posted mark at once; moving to another clip or
 * closing the viewer saves what is still waiting.
 */
export function usePostDraft({
  jobId,
  clip,
  detail,
  fallbackTitle,
  credit,
  editable,
}: {
  jobId: string;
  clip: string;
  detail?: ClipDetail;
  fallbackTitle: string;
  credit: string;
  editable: boolean;
}) {
  const [forClip, setForClip] = useState(clip);
  const [draft, setDraft] = useState(() =>
    draftOf(detail, fallbackTitle, credit),
  );
  const [posted, setPosted] = useState(detail?.post?.posted ?? {});
  const [state, setState] = useState<SaveState>("idle");
  // Another clip: start from its saved text (React's way to reset state from a prop).
  if (forClip !== clip) {
    setForClip(clip);
    setDraft(draftOf(detail, fallbackTitle, credit));
    setPosted(detail?.post?.posted ?? {});
    setState("idle");
  }

  const pending = useRef<Pending | null>(null);
  const failed = useRef<Pending | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const savedTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(savedTimer.current), []);
  const current = useRef(clip);
  current.current = clip;

  const send = useCallback(
    async ({ clip: name, change }: Pending) => {
      const mine = name === current.current;
      if (mine) setState("saving");
      try {
        const response = await fetch(
          `/api/jobs/${jobId}/clips/${name.replace(/\.mp4$/, "")}/post`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(change),
          },
        );
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as {
            error?: string;
          };
          throw new Error(body.error ?? `HTTP ${response.status}`);
        }
        failed.current = null;
        if (name === current.current) {
          setState("saved");
          window.clearTimeout(savedTimer.current);
          savedTimer.current = window.setTimeout(
            () => setState((s) => (s === "saved" ? "idle" : s)),
            SAVED_SHOWN_MS,
          );
        }
        void jobsChanged(jobId);
      } catch (error) {
        failed.current = { clip: name, change };
        if (name === current.current) setState("error");
        else
          toast.error(
            `Couldn't save the post text: ${(error as Error).message}`,
          );
      }
    },
    [jobId],
  );

  const flush = useCallback(() => {
    window.clearTimeout(timer.current);
    const waiting = pending.current;
    pending.current = null;
    if (waiting) void send(waiting);
  }, [send]);

  // Leaving a clip (or closing the viewer) saves what is still waiting for it.
  useEffect(() => flush, [clip, flush]);

  const edit = (field: keyof PostDraft, value: string) => {
    if (!editable) return;
    setDraft((d) => ({ ...d, [field]: value }));
    const before = pending.current?.clip === clip ? pending.current.change : {};
    pending.current = { clip, change: { ...before, [field]: value } };
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, SAVE_DELAY_MS);
  };

  const togglePosted = (platform: Platform) => {
    if (!editable) return;
    flush();
    const on = !posted[platform];
    setPosted((p) => ({
      ...p,
      [platform]: on ? (p[platform] ?? new Date().toISOString()) : null,
    }));
    void send({ clip, change: { posted: { [platform]: on } } });
  };

  /** Leaving a field saves it at once; the hashtags then show as they were saved. */
  const leave = (field: keyof PostDraft) => {
    if (field === "hashtags" && editable)
      setDraft((d) => ({
        ...d,
        hashtags: cleanHashtags(d.hashtags).join(" "),
      }));
    flush();
  };

  const retry = () => {
    if (failed.current) void send(failed.current);
  };

  return { draft, posted, state, edit, leave, togglePosted, retry };
}

export type PostDraftApi = ReturnType<typeof usePostDraft>;

const field =
  "bg-muted/60 focus-visible:ring-ring w-full rounded-lg border px-3 py-2 text-sm leading-snug outline-none focus-visible:ring-2 read-only:opacity-80";

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

/**
 * The clip's post text: title, caption, hashtags and credit, each with a Copy button,
 * plus Copy all and where it has been posted. Read-only for a re-rank's new set (until
 * it is kept) and while the video runs.
 */
export function PostPanel({
  api,
  editable,
  hasPost,
  readOnlyNote,
}: {
  api: PostDraftApi;
  editable: boolean;
  /** Gemini wrote text for this clip (older clips have none). */
  hasPost: boolean;
  readOnlyNote?: string;
}) {
  const { draft, posted, state, edit, leave, togglePosted, retry } = api;
  const [copied, setCopied] = useState<string | null>(null);
  const id = useId();
  const copyTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(copyTimer.current), []);

  const copy = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(null), 1500);
    } catch {
      toast.error("Couldn't copy to the clipboard");
    }
  };

  const tags = cleanHashtags(draft.hashtags).join(" ");
  const rows: {
    key: keyof PostDraft;
    label: string;
    copyText: string;
    multiline?: boolean;
    limit?: number;
    placeholder?: string;
  }[] = [
    {
      key: "title",
      label: "Title",
      copyText: draft.title,
      limit: POST_LIMITS.title,
      placeholder: "Shorts title",
    },
    {
      key: "caption",
      label: "Caption",
      copyText: draft.caption,
      multiline: true,
      placeholder: "What the viewer gets, then a question",
    },
    {
      key: "hashtags",
      label: "Hashtags",
      copyText: tags,
      placeholder: "#podcast #startups",
    },
    {
      key: "credit",
      label: "Credit",
      copyText: draft.credit,
      placeholder: "Clip from <show>, full episode: <link>",
    },
  ];

  const copyButton = (key: string, label: string, text: string) => (
    <button
      type="button"
      aria-label={
        copied === key ? `${label} copied` : `Copy ${label.toLowerCase()}`
      }
      disabled={!text.trim()}
      onClick={() => void copy(key, text)}
      className="text-muted-foreground hover:text-foreground hover:bg-accent press inline-flex size-7 items-center justify-center rounded-md disabled:opacity-40 pointer-coarse:size-11"
    >
      {copied === key ? (
        <Check className="text-foreground pop-in size-3.5" aria-hidden />
      ) : (
        <Copy className="size-3.5" aria-hidden />
      )}
    </button>
  );

  return (
    <section aria-labelledby={`${id}-heading`} className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 id={`${id}-heading`} className="text-sm font-semibold">
          Post
        </h3>
        <button
          type="button"
          onClick={() => void copy("all", postAll(draft))}
          className="bg-muted hover:bg-accent press inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium pointer-coarse:h-11"
        >
          {copied === "all" ? (
            <Check className="pop-in size-3.5" aria-hidden />
          ) : (
            <Copy className="size-3.5" aria-hidden />
          )}
          {copied === "all" ? "Copied" : "Copy all"}
        </button>
      </div>
      {!editable && readOnlyNote ? (
        <p className="text-muted-foreground text-xs leading-relaxed">
          {readOnlyNote}
        </p>
      ) : !hasPost ? (
        <p className="text-muted-foreground text-xs leading-relaxed">
          Gemini wrote no text for this clip. Type your own; it saves as you go.
        </p>
      ) : null}

      {rows.map((row) => {
        const inputId = `${id}-${row.key}`;
        const value = draft[row.key];
        const over = row.limit != null && value.length > row.limit;
        return (
          <div key={row.key} className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <label
                htmlFor={inputId}
                className="text-muted-foreground text-xs font-medium"
              >
                {row.label}
              </label>
              <div className="flex items-center gap-1">
                {row.limit != null && (
                  <span
                    className={cn(
                      "tabular text-xs",
                      over ? "text-destructive" : "text-muted-foreground",
                    )}
                  >
                    {value.length}/{row.limit}
                  </span>
                )}
                {copyButton(row.key, row.label, row.copyText)}
              </div>
            </div>
            {row.multiline ? (
              <textarea
                id={inputId}
                value={value}
                rows={4}
                readOnly={!editable}
                maxLength={POST_LIMITS.caption}
                placeholder={row.placeholder}
                onChange={(e) => edit(row.key, e.target.value)}
                onBlur={() => leave(row.key)}
                className={cn(field, "resize-y")}
              />
            ) : (
              <input
                id={inputId}
                type="text"
                value={value}
                readOnly={!editable}
                maxLength={row.limit ?? POST_LIMITS.credit}
                placeholder={row.placeholder}
                onChange={(e) => edit(row.key, e.target.value)}
                onBlur={() => leave(row.key)}
                className={cn(field, "h-9 pointer-coarse:h-11")}
              />
            )}
          </div>
        );
      })}

      <div className="space-y-1.5">
        <p className="text-muted-foreground text-xs font-medium">Posted on</p>
        <div className="flex gap-2">
          {PLATFORMS.map((platform) => {
            const at = posted[platform.id];
            return (
              <button
                key={platform.id}
                type="button"
                aria-pressed={Boolean(at)}
                disabled={!editable}
                onClick={() => togglePosted(platform.id)}
                className={cn(
                  "press inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium disabled:opacity-50 pointer-coarse:h-11",
                  at
                    ? "border-tally bg-tally-soft text-foreground"
                    : "hover:bg-accent text-muted-foreground",
                )}
              >
                {at && <Check className="pop-in size-3.5" aria-hidden />}
                {platform.label}
                {at && (
                  <span className="text-muted-foreground tabular text-xs font-normal">
                    {shortDate(at)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <p
        aria-live="polite"
        className={cn(
          "flex min-h-5 items-center gap-1.5 text-xs",
          state === "error" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {state === "saving" && "Saving…"}
        {state === "saved" && (
          <>
            <Check className="text-foreground size-3.5" aria-hidden />
            Saved
          </>
        )}
        {state === "error" && (
          <>
            Couldn&apos;t save.
            <button
              type="button"
              onClick={retry}
              className="text-foreground inline-flex items-center gap-1 font-medium underline underline-offset-2"
            >
              <RotateCw className="size-3" aria-hidden />
              Try again
            </button>
          </>
        )}
      </p>
    </section>
  );
}
