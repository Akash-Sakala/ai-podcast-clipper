// Browser only: sending an MP4 to the app, with upload progress.

/** POSTs `file` as the raw body (XMLHttpRequest, not fetch, so large uploads can show progress). */
export function uploadVideo(
  url: string,
  file: File,
  onProgress: (percent: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "video/mp4");
    xhr.setRequestHeader("x-filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      let message = `Upload failed (${xhr.status})`;
      try {
        message =
          (JSON.parse(xhr.responseText) as { error?: string }).error ?? message;
      } catch {}
      reject(new Error(message));
    };
    xhr.onerror = () =>
      reject(new Error("The connection dropped during upload"));
    xhr.send(file);
  });
}

/** Opens the file chooser for one MP4; resolves null when it is closed without a pick. */
export function pickVideoFile(): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "video/mp4,.mp4";
    input.addEventListener("change", () => resolve(input.files?.[0] ?? null), {
      once: true,
    });
    input.addEventListener("cancel", () => resolve(null), { once: true });
    input.click();
  });
}
