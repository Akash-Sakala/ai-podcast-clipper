// Shared between server code and client components (no Node.js imports here).

/**
 * A quick content fingerprint for uploaded videos, used to spot a video that was
 * already processed even if it was renamed: SHA-256 of the size plus 1 MiB from the
 * start, middle and end (the whole file when it is under 3 MiB). The browser computes
 * it before uploading; the server computes the same for files already on disk.
 */
export const FINGERPRINT_CHUNK = 1024 * 1024;

export function fingerprintRanges(size: number): [number, number][] {
  if (size <= 3 * FINGERPRINT_CHUNK) return [[0, size]];
  const middle = Math.floor(size / 2) - FINGERPRINT_CHUNK / 2;
  return [
    [0, FINGERPRINT_CHUNK],
    [middle, middle + FINGERPRINT_CHUNK],
    [size - FINGERPRINT_CHUNK, size],
  ];
}

export function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

/** Browser: fingerprint a File before uploading it (null if crypto is unavailable). */
export async function fingerprintFile(file: Blob): Promise<string | null> {
  if (typeof crypto === "undefined" || !crypto.subtle) return null;
  const parts: BlobPart[] = [`${file.size}:`];
  for (const [start, end] of fingerprintRanges(file.size)) {
    parts.push(file.slice(start, end));
  }
  const data = await new Blob(parts).arrayBuffer();
  return toHex(await crypto.subtle.digest("SHA-256", data));
}
