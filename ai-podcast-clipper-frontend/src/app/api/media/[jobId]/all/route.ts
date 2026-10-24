import fs from "node:fs";
import fsp from "node:fs/promises";
import { clipPath, isValidClipName, isValidJobId, readJob } from "~/lib/jobs";

export const dynamic = "force-dynamic";

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

function crcUpdate(crc: number, chunk: Uint8Array) {
  let c = crc;
  for (const byte of chunk) {
    c = CRC_TABLE[(c ^ byte) & 0xff]! ^ (c >>> 8);
  }
  return c >>> 0;
}

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

async function* zipEntries(files: { name: string; path: string }[]) {
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

    let crc = 0xffffffff;
    let size = 0;
    for await (const chunk of fs.createReadStream(file.path)) {
      const bytes = chunk as Buffer;
      crc = crcUpdate(crc, bytes);
      size += bytes.length;
      yield bytes;
    }
    crc = (crc ^ 0xffffffff) >>> 0;

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
  const files: { name: string; path: string }[] = [];
  for (const clip of job.clips) {
    if (!isValidClipName(clip)) continue;
    const file = clipPath(jobId, clip);
    try {
      await fsp.access(file);
    } catch {
      continue;
    }
    const detail = details.get(clip);
    const rank = detail?.rank ?? files.length + 1;
    files.push({
      name: `${String(rank).padStart(2, "0")} ${safeName(detail?.title ?? clip.replace(".mp4", ""))}.mp4`,
      path: file,
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
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="clips.zip"; filename*=UTF-8''${encodeURIComponent(`${safeName(job.title)} clips.zip`)}`,
      "Cache-Control": "no-store",
    },
  });
}
