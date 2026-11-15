import fs from "node:fs";
import fsp from "node:fs/promises";
import zlib from "node:zlib";
import { readJob } from "~/lib/job-store";
import { clipPath, isValidClipName, isValidJobId } from "~/lib/paths";

/**
 * "Download all": streams the job's clips as one .zip (stored, no compression -
 * MP4 is already compressed). Entries are named by rank and title.
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/** Table-driven CRC-32 for Node versions without zlib.crc32 (added in 22.2 / 20.15). */
function jsCrc32(chunk: Uint8Array, previous: number) {
  let c = (previous ^ 0xffffffff) >>> 0;
  for (const byte of chunk) {
    c = CRC_TABLE[(c ^ byte) & 0xff]! ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

const nativeCrc32 = (
  zlib as { crc32?: (data: Uint8Array, value?: number) => number }
).crc32;

/** CRC-32 of `chunk` continuing from `previous` (0 to start), like zlib.crc32. */
const crc32 = (chunk: Uint8Array, previous: number) =>
  nativeCrc32 ? nativeCrc32(chunk, previous) : jsCrc32(chunk, previous);

function dosDateTime(date: Date) {
  const time =
    (date.getHours() << 11) |
    (date.getMinutes() << 5) |
    Math.floor(date.getSeconds() / 2);
  const day =
    ((date.getFullYear() - 1980) << 9) |
    ((date.getMonth() + 1) << 5) |
    date.getDate();
  return { time, day };
}

function safeName(text: string) {
  return (
    text
      .replace(/[^\p{L}\p{N} _-]+/gu, "")
      .trim()
      .replace(/\s+/g, " ")
      .slice(0, 60) || "clip"
  );
}

type ZipFile = { name: string; path: string; size: number };

/** Bytes of the finished archive: every entry is stored, so it is known up front. */
function zipLength(files: ZipFile[]) {
  let total = 22; // end of central directory
  for (const file of files) {
    const name = Buffer.byteLength(file.name, "utf8");
    total += 30 + name + file.size + 16 + (46 + name);
  }
  return total;
}

async function* zipEntries(files: ZipFile[]) {
  const central: Buffer[] = [];
  let offset = 0;
  const { time, day } = dosDateTime(new Date());

  for (const file of files) {
    const name = Buffer.from(file.name, "utf8");
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0808, 6); // data descriptor + UTF-8 names
    local.writeUInt16LE(0, 8); // stored
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(day, 12);
    local.writeUInt16LE(name.length, 26);
    yield local;
    yield name;

    // Exactly the size measured for Content-Length: a clip replaced by a re-rank in
    // the meantime must fail the download, not produce a short or long archive.
    let crc = 0;
    let size = 0;
    if (file.size > 0) {
      for await (const chunk of fs.createReadStream(file.path, {
        start: 0,
        end: file.size - 1,
      })) {
        const bytes = chunk as Buffer;
        crc = crc32(bytes, crc);
        size += bytes.length;
        yield bytes;
      }
    }
    if (size !== file.size) {
      throw new Error(`${file.name} changed while it was being zipped`);
    }

    const descriptor = Buffer.alloc(16);
    descriptor.writeUInt32LE(0x08074b50, 0);
    descriptor.writeUInt32LE(crc, 4);
    descriptor.writeUInt32LE(size, 8);
    descriptor.writeUInt32LE(size, 12);
    yield descriptor;

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0808, 8);
    entry.writeUInt16LE(0, 10);
    entry.writeUInt16LE(time, 12);
    entry.writeUInt16LE(day, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(size, 20);
    entry.writeUInt32LE(size, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);
    offset += 30 + name.length + size + 16;
  }

  const centralBuffer = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralBuffer.length, 12);
  end.writeUInt32LE(offset, 16);
  yield centralBuffer;
  yield end;
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  if (!isValidJobId(jobId)) return new Response("Not found", { status: 404 });
  const job = await readJob(jobId);
  if (!job || job.clips.length === 0) {
    return new Response("Not found", { status: 404 });
  }

  const details = new Map((job.clipDetails ?? []).map((d) => [d.name, d]));
  const files: ZipFile[] = [];
  for (const clip of job.clips) {
    if (!isValidClipName(clip)) continue;
    const file = clipPath(jobId, clip);
    let size: number;
    try {
      size = (await fsp.stat(file)).size;
    } catch {
      continue;
    }
    const detail = details.get(clip);
    const rank = detail?.rank ?? files.length + 1;
    files.push({
      name: `${String(rank).padStart(2, "0")} ${safeName(detail?.title ?? clip.replace(".mp4", ""))}.mp4`,
      path: file,
      size,
    });
  }
  files.sort((a, b) => a.name.localeCompare(b.name));

  const iterator = zipEntries(files);
  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { value, done } = await iterator.next();
      if (done) controller.close();
      else controller.enqueue(new Uint8Array(value));
    },
    // The browser gave up (cancelled the download): close the open clip file now.
    async cancel() {
      await iterator.return(undefined);
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Length": String(zipLength(files)),
      "Content-Disposition": `attachment; filename="clips.zip"; filename*=UTF-8''${encodeURIComponent(`${safeName(job.title)} clips.zip`)}`,
      "Cache-Control": "no-store",
    },
  });
}
