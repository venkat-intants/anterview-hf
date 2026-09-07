// Folder-aware drag-and-drop for the apply form.
//
// Two things a plain <input type="file"> cannot do, and one it should not be
// asked to:
//
//   * a DROPPED folder arrives as a DataTransferItem, not a File. Reading it
//     means walking the entry tree through `webkitGetAsEntry`, which is
//     non-standard, callback-based, and present in every browser that matters.
//     `e.dataTransfer.files` is empty for a folder drop, so a handler that
//     only reads that silently accepts nothing — the user drops 30 CVs and the
//     page looks broken.
//
//   * a CLICKED folder needs `webkitdirectory` on the input, which yields a
//     flat FileList with relative paths. Offered alongside the drop because
//     folder DROP is unreliable outside Chromium, while the picker is not.
//
// Everything here is filtering and traversal only — no network, no state. The
// page owns what to do with the files it gets back.

/** A dropped entry, narrowed to the parts actually used. */
interface FsEntry {
  isFile: boolean;
  isDirectory: boolean;
  file: (cb: (f: File) => void, err?: (e: unknown) => void) => void;
  createReader: () => {
    readEntries: (cb: (entries: FsEntry[]) => void, err?: (e: unknown) => void) => void;
  };
}

/** Depth cap. A symlink loop or a deep tree should not hang the tab. */
const MAX_DEPTH = 6;

/** Total files read from one drop. Well past any realistic CV folder. */
const MAX_FILES = 500;

export function isPdf(file: File): boolean {
  return (
    file.type === 'application/pdf' ||
    // Some systems report an empty or generic type for a dragged file; the
    // extension is the only signal left, and the server re-checks the bytes.
    file.name.toLowerCase().endsWith('.pdf')
  );
}

function readEntry(entry: FsEntry): Promise<File | null> {
  return new Promise((resolve) => {
    entry.file(
      (f) => resolve(f),
      () => resolve(null),
    );
  });
}

/**
 * Read one directory fully.
 *
 * `readEntries` returns a BATCH, not the whole listing — it must be called
 * repeatedly until it yields an empty array. A single call looks correct on a
 * small folder and silently truncates at about 100 entries on a large one,
 * which is the exact size where someone would notice a seeding run came up
 * short and not know why.
 */
function readDirectory(entry: FsEntry): Promise<FsEntry[]> {
  const reader = entry.createReader();
  const all: FsEntry[] = [];
  return new Promise((resolve) => {
    const readBatch = (): void => {
      reader.readEntries(
        (batch) => {
          if (batch.length === 0) {
            resolve(all);
            return;
          }
          all.push(...batch);
          readBatch();
        },
        () => resolve(all),
      );
    };
    readBatch();
  });
}

async function walk(entry: FsEntry, depth: number, out: File[]): Promise<void> {
  if (out.length >= MAX_FILES || depth > MAX_DEPTH) return;
  if (entry.isFile) {
    const file = await readEntry(entry);
    if (file && isPdf(file)) out.push(file);
    return;
  }
  if (!entry.isDirectory) return;
  for (const child of await readDirectory(entry)) {
    if (out.length >= MAX_FILES) return;
    await walk(child, depth + 1, out);
  }
}

/**
 * Every PDF in a drop, whether it was files, folders, or both.
 *
 * The entries have to be captured synchronously: `dataTransfer.items` is
 * emptied as soon as the event handler returns, so reading it after an `await`
 * yields nothing. That failure is intermittent and looks like a flaky browser.
 */
export async function filesFromDrop(dataTransfer: DataTransfer): Promise<File[]> {
  const entries: FsEntry[] = [];
  const plainFiles: File[] = [];

  for (const item of Array.from(dataTransfer.items ?? [])) {
    if (item.kind !== 'file') continue;
    // Cast through unknown: the DOM lib types webkitGetAsEntry as returning
    // FileSystemEntry, which declares neither `file` nor `createReader` —
    // those live on the FileSystemFileEntry / FileSystemDirectoryEntry
    // subtypes the runtime actually hands back. FsEntry is the union of what
    // is used, narrowed by the isFile / isDirectory flags below.
    const asEntry = (
      item as DataTransferItem & { webkitGetAsEntry?: () => unknown }
    ).webkitGetAsEntry?.() as FsEntry | null | undefined;
    if (asEntry) {
      entries.push(asEntry);
    } else {
      // No entry API (older Safari): a dropped FILE still arrives here, and a
      // dropped folder is simply not readable. Better to take the files we can
      // than to take nothing.
      const file = item.getAsFile();
      if (file) plainFiles.push(file);
    }
  }

  const out: File[] = [];
  for (const entry of entries) {
    await walk(entry, 0, out);
  }
  for (const file of plainFiles) {
    if (isPdf(file) && out.length < MAX_FILES) out.push(file);
  }

  // Stable order so a seeding run is reproducible and the progress list does
  // not appear to shuffle itself between renders.
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * A plausible candidate name from a CV filename.
 *
 * "priya_sharma_resume_v2.pdf" -> "Priya Sharma". Best-effort by design: this
 * is a placeholder for seeded test data, and the real name is pulled out of
 * the PDF by the scorer once the reconciler reaches the row.
 */
export function nameFromFilename(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, '');
  const words = base
    .split(/[\s_\-.]+/)
    .filter((w) => w.length > 1 && !/^(cv|resume|final|new|v\d+|\d+)$/i.test(w))
    .slice(0, 3);
  const name = words
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
    .trim();
  return name.length >= 2 ? name.slice(0, 200) : 'Candidate';
}

/**
 * A unique, obviously-fake address for one seeded application.
 *
 * `example.com` and not `.invalid`, despite `.invalid` being the more obviously
 * unroutable of the two: the server validates this field with pydantic's
 * `EmailStr`, which REJECTS the special-use TLDs — `.invalid`, `.test`,
 * `.localhost` — so every seeded application 422'd before this. `example.com`
 * is reserved by RFC 2606 for exactly this purpose, is never accepted for mail
 * by IANA, and passes the validator.
 *
 * It has to be unroutable, not merely fake: committing a hire or reject
 * decision on one of these candidates does try to send them an email.
 *
 * The random suffix keeps two CVs with similar filenames from colliding on the
 * server's one-application-per-opening rule and being reported as duplicates
 * instead of being seeded.
 */
export function seedEmailFor(filename: string): string {
  const slug =
    filename
      .replace(/\.[^.]+$/, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '.')
      .replace(/^\.|\.$/g, '')
      .slice(0, 40) || 'candidate';
  const suffix = Math.random().toString(36).slice(2, 8);
  return `${slug}.${suffix}@seed.example.com`;
}
