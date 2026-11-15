import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { fingerprintFile } from "~/lib/fingerprint";
import { uploadVideo } from "~/lib/upload";
import { parseYoutubeLink } from "~/lib/youtube";
import type { Duplicate } from "./duplicate-notice";

type Pending = { kind: "url"; url: string } | { kind: "file"; file: File };

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
 */
export function useAddVideo(onCreated: () => Promise<void>) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [upload, setUpload] = useState<{
    name: string;
    percent: number;
  } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [checking, setChecking] = useState(false);
  const [duplicate, setDuplicate] = useState<{
    job: Duplicate;
    pending: Pending;
  } | null>(null);
  const busy = submitting || checking || upload !== null;

  /** Uploads a new video, or (rerankJobId) the same video again to re-rank its job. */
  const startUpload = async (file: File, rerankJobId?: string) => {
    setUpload({ name: file.name, percent: 0 });
    try {
      await uploadVideo(
        rerankJobId ? `/api/jobs/${rerankJobId}/rerank` : "/api/jobs",
        file,
        (percent) => setUpload({ name: file.name, percent }),
      );
      toast.success(rerankJobId ? "Re-ranking" : "Video added", {
        description: rerankJobId
          ? "Reusing the transcript. The current clips stay until the new ones are ready."
          : "Clips will appear here when they're ready.",
      });
      if (rerankJobId) router.push(`/videos/${rerankJobId}`);
      else await onCreated();
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
    const value = url.trim();
    if (!value) return;
    if (!parseYoutubeLink(value)) {
      setInvalid(true);
      return;
    }
    setDuplicate(null);
    setChecking(true);
    const job = await findDuplicate({ youtubeUrl: value });
    setChecking(false);
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

  const addUrl = async (value: string) => {
    setSubmitting(true);
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ youtubeUrl: value }),
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
