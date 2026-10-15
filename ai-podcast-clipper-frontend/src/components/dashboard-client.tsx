"use client";

import { Loader2, UploadCloud, Youtube } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import { ACTIVE_STATUSES, type Job, type JobStatus } from "~/lib/job-types";
import { ClipDisplay } from "./clip-display";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "./ui/card";
import { Input } from "./ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

const POLL_INTERVAL_MS = 4000;

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  downloading: "Downloading",
  uploading: "Uploading to Modal",
  processing: "Processing on GPU",
  saving: "Saving clips",
  done: "Done",
  failed: "Failed",
};

function uploadFile(file: File, onProgress: (percent: number) => void) {
  // XMLHttpRequest (not fetch) so we can show upload progress for large files.
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.setRequestHeader("Content-Type", "video/mp4");
    xhr.setRequestHeader("x-filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        let message = `Upload failed with status ${xhr.status}`;
        try {
          message =
            (JSON.parse(xhr.responseText) as { error?: string }).error ??
            message;
        } catch {}
        reject(new Error(message));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(file);
  });
}

export function DashboardClient({
  initialJobs,
  jobsDir,
}: {
  initialJobs: Job[];
  jobsDir: string;
}) {
  const [jobs, setJobs] = useState<Job[]>(initialJobs);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [submittingUrl, setSubmittingUrl] = useState(false);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: (accepted) => setFiles(accepted),
    onDropRejected: () =>
      toast.error("File not accepted", {
        description: "Choose a single .mp4 file up to 4 GB.",
      }),
    accept: { "video/mp4": [".mp4"] },
    maxSize: 4 * 1024 * 1024 * 1024,
    maxFiles: 1,
    disabled: uploading,
  });

  const refreshJobs = useCallback(async () => {
    try {
      const response = await fetch("/api/jobs", { cache: "no-store" });
      if (response.ok) setJobs((await response.json()) as Job[]);
    } catch {
      // dev server restarting - try again on the next poll
    }
  }, []);

  const hasActiveJobs = jobs.some((job) =>
    ACTIVE_STATUSES.includes(job.status),
  );

  // Poll while something is running.
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setInterval(() => void refreshJobs(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasActiveJobs, refreshJobs]);

  const handleUpload = async () => {
    const file = files[0];
    if (!file) return;

    setUploading(true);
    setUploadProgress(0);
    try {
      await uploadFile(file, setUploadProgress);
      setFiles([]);
      toast.success("Video uploaded", {
        description: "Processing has started. Status updates below.",
      });
      await refreshJobs();
    } catch (error) {
      toast.error("Upload failed", { description: (error as Error).message });
    } finally {
      setUploading(false);
    }
  };

  const handleYoutube = async () => {
    if (!youtubeUrl.trim()) return;

    setSubmittingUrl(true);
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ youtubeUrl }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(body.error ?? `Request failed (${response.status})`);
      }
      setYoutubeUrl("");
      toast.success("YouTube job started", {
        description: "Downloading the video, then processing on the GPU.",
      });
      await refreshJobs();
    } catch (error) {
      toast.error("Could not start job", {
        description: (error as Error).message,
      });
    } finally {
      setSubmittingUrl(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-5xl flex-col space-y-6 px-4 py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Podcast Clipper
        </h1>
        <p className="text-muted-foreground">
          Turn a podcast into vertical clips with subtitles. Clips are saved on
          this computer.
        </p>
      </div>

      <Tabs defaultValue="upload">
        <TabsList>
          <TabsTrigger value="upload">New job</TabsTrigger>
          <TabsTrigger value="clips">Clips</TabsTrigger>
        </TabsList>

        <TabsContent value="upload" className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle>From YouTube</CardTitle>
              <CardDescription>
                Paste a video link. It is downloaded on this computer (up to
                1080p).
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleYoutube();
                }}
              >
                <Input
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={youtubeUrl}
                  onChange={(event) => setYoutubeUrl(event.target.value)}
                  disabled={submittingUrl}
                />
                <Button
                  type="submit"
                  disabled={!youtubeUrl.trim() || submittingUrl}
                >
                  {submittingUrl ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Youtube className="mr-2 h-4 w-4" />
                  )}
                  Generate clips
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>From a file</CardTitle>
              <CardDescription>
                Upload an MP4 video from this computer.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div
                {...getRootProps()}
                className={`cursor-pointer rounded-lg border-2 border-dashed transition-colors ${
                  isDragActive
                    ? "border-primary bg-muted"
                    : "border-muted-foreground/25"
                } ${uploading ? "pointer-events-none opacity-50" : ""}`}
              >
                <input {...getInputProps()} />
                <div className="flex flex-col items-center justify-center space-y-4 p-10 text-center">
                  <UploadCloud className="text-muted-foreground h-12 w-12" />
                  <p className="font-medium">
                    {isDragActive
                      ? "Drop the file here"
                      : "Drag and drop your file"}
                  </p>
                  <p className="text-muted-foreground text-sm">
                    or click to browse (MP4 up to 4 GB)
                  </p>
                  <Button
                    type="button"
                    variant="default"
                    size="sm"
                    disabled={uploading}
                  >
                    Select File
                  </Button>
                </div>
              </div>

              <div className="mt-2 flex items-start justify-between">
                <div className="text-sm">
                  {files[0] && (
                    <>
                      <p className="font-medium">Selected file:</p>
                      <p className="text-muted-foreground">{files[0].name}</p>
                    </>
                  )}
                </div>
                <Button
                  disabled={files.length === 0 || uploading}
                  onClick={handleUpload}
                >
                  {uploading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Uploading {uploadProgress}%
                    </>
                  ) : (
                    "Upload and Generate Clips"
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>

          {jobs.length > 0 && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Jobs</CardTitle>
                    <CardDescription>
                      {hasActiveJobs
                        ? "Updating automatically while jobs are running."
                        : "All jobs finished."}
                    </CardDescription>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void refreshJobs()}
                  >
                    Refresh
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="max-h-[400px] overflow-auto rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Video</TableHead>
                        <TableHead>Started</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Details</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {jobs.map((job) => (
                        <TableRow key={job.id}>
                          <TableCell
                            className="max-w-xs truncate font-medium"
                            title={job.title}
                          >
                            {job.source === "youtube" && (
                              <Youtube className="mr-1 inline h-4 w-4 text-red-500" />
                            )}
                            {job.title}
                          </TableCell>
                          <TableCell className="text-muted-foreground text-sm">
                            {new Date(job.createdAt).toLocaleString()}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                job.status === "failed"
                                  ? "destructive"
                                  : "outline"
                              }
                            >
                              {ACTIVE_STATUSES.includes(job.status) && (
                                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              )}
                              {STATUS_LABELS[job.status] ?? job.status}
                            </Badge>
                          </TableCell>
                          <TableCell
                            className="text-muted-foreground max-w-sm text-sm whitespace-normal"
                            title={job.error ?? undefined}
                          >
                            {job.status === "failed" ? job.error : job.message}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="clips">
          <Card>
            <CardHeader>
              <CardTitle>Clips</CardTitle>
              <CardDescription>
                Newest first. Files live in {jobsDir}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ClipDisplay jobs={jobs} jobsDir={jobsDir} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
