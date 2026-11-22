import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { fingerprintFile } from "~/lib/fingerprint";
import { jobsChanged } from "~/lib/job-sync";
import { uploadVideo } from "~/lib/upload";
import { parseYoutubeLink } from "~/lib/youtube";
import type { Duplicate } from "./duplicate-notice";

type Pending = { kind: "url"; url: string } | { kind: "file"; file: File };

/** A YouTube link is looked up this long after the last keystroke. */
const LOOKUP_DELAY_MS = 350;

export type LinkState = "empty" | "valid" | "invalid";

export type UploadState = {
  name: string;
  percent: number;
  loaded: number;
  total: number;
  startedAt: number;
};

async function findDuplicate(query: {
  youtubeUrl?: string;
  fingerprint?: string;
}): Promise<Duplicate | null> {
  try {
    const response = await fetch("/api/jobs/duplicate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
    });
    if (!response.ok) return null;
    return ((await response.json()) as { duplicate: Duplicate | null })
      .duplicate;
  } catch {
    return null; // never block adding a video because the check failed
  }
}

/**
 * Adding a video, from a YouTube link or an MP4: check whether it was processed
 * before (showing `duplicate` if so), then create the job or upload the file.
 * A YouTube link is checked as soon as it is typed or pasted, so a duplicate shows
 * before "Make clips" is pressed.
 */
export function useAddVideo(onCreated: () => Promise<void>) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [checking, setChecking] = useState(false);
  const [duplicate, setDuplicate] = useState<{
    job: Duplicate;
    pending: Pending;
  } | null>(null);
  /** The last link looked up and what was found (a job added before, or none). */
  const [lookup, setLookup] = useState<{
    url: string;
    job: Duplicate | null;
  } | null>(null);
  const busy = submitting || checking || upload !== null;

  const value = url.trim();
  const linkState: LinkState = !value
    ? "empty"
    : parseYoutubeLink(value)
      ? "valid"
      : "invalid";
  const lookingUp = linkState === "valid" && lookup?.url !== value;

  // A link typed or pasted is looked up in the background. A link changed away from a
  // duplicate's link drops that notice.
  const duplicateRef = useRef(duplicate);
  duplicateRef.current = duplicate;
  useEffect(() => {
    const shown = duplicateRef.current;
    if (shown?.pending.kind === "url" && shown.pending.url !== value)
      setDuplicate(null);
    if (!parseYoutubeLink(value)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void findDuplicate({ youtubeUrl: value }).then((job) => {
        if (cancelled) return;
        setLookup({ url: value, job });
        if (job) setDuplicate({ job, pending: { kind: "url", url: value } });
      });
    }, LOOKUP_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [value]);

  /** Uploads a new video, or (rerankJobId) the same video again to re-rank its job. */
  const startUpload = async (file: File, rerankJobId?: string) => {
    const startedAt = Date.now();
    const state = (percent: number, loaded: number): UploadState => ({
      name: file.name,
      percent,
      loaded,
      total: file.size,
      startedAt,
    });
    setUpload(state(0, 0));
    try {
      await uploadVideo(
        rerankJobId ? `/api/jobs/${rerankJobId}/rerank` : "/api/jobs",
        file,
        (percent, loaded) => setUpload(state(percent, loaded)),
      );
      toast.success(rerankJobId ? "Re-ranking" : "Video added", {
        description: rerankJobId
          ? "Reusing the transcript. The current clips stay until the new ones are ready."
          : "Clips will appear here when they're ready.",
      });
      if (rerankJobId) {
        void jobsChanged(rerankJobId);
        router.push(`/videos/${rerankJobId}`);
      } else await onCreated();
    } catch (error) {
      toast.error(rerankJobId ? "Couldn't re-rank" : "Upload failed", {
        description: (error as Error).message,
      });
    } finally {
      setUpload(null);
    }
  };

  // Before adding anything, check whether this video was processed already.
  const checkFile = async (file: File) => {
    setDuplicate(null);
    setChecking(true);
    const fingerprint = await fingerprintFile(file).catch(() => null);
    const job = fingerprint ? await findDuplicate({ fingerprint }) : null;
    setChecking(false);
    if (job) setDuplicate({ job, pending: { kind: "file", file } });
    else await startUpload(file);
  };

  const submitUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!value) return;
    if (!parseYoutubeLink(value)) {
      setInvalid(true);
      return;
    }
    // The notice for this link is already showing: its buttons say what happens next.
    if (duplicate?.pending.kind === "url" && duplicate.pending.url === value)
      return;
    let job = lookup?.url === value ? lookup.job : undefined;
    if (job === undefined) {
      setChecking(true);
      job = await findDuplicate({ youtubeUrl: value });
      setChecking(false);
      setLookup({ url: value, job });
    }
    // Added before (also after its notice was closed): show the choices again.
    if (job) {
      setDuplicate({ job, pending: { kind: "url", url: value } });
      return;
    }
    await addUrl(value);
  };

  const addAgain = () => {
    if (!duplicate) return;
    const { pending } = duplicate;
    setDuplicate(null);
    if (pending.kind === "file") void startUpload(pending.file);
    else void addUrl(pending.url);
  };

  const addUrl = async (link: string) => {
    setSubmitting(true);
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ youtubeUrl: link }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(body.error ?? `Request failed (${response.status})`);
      }
      setUrl("");
      toast.success("Video added", {
        description: "Clips will appear here when they're ready.",
      });
      await onCreated();
    } catch (error) {
      toast.error("Couldn't add this video", {
        description: (error as Error).message,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return {
    url,
    setUrl,
    linkState,
    lookingUp,
    invalid,
    setInvalid,
    submitting,
    checking,
    upload,
    duplicate,
    busy,
    checkFile,
    submitUrl,
    addAgain,
    /** Re-rank the duplicate job with the dropped file (its video was deleted). */
    rerankWithFile: (file: File) => {
      if (!duplicate) return;
      const id = duplicate.job.id;
      setDuplicate(null);
      void startUpload(file, id);
    },
    dismissDuplicate: () => setDuplicate(null),
  };
}
