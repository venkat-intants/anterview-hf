// Folder-aware drop handling for the apply form.
//
// Pure functions, so tested directly. Two of these guard failures that look
// like a broken browser rather than a bug:
//
//   * `dataTransfer.files` is EMPTY for a folder drop — the whole reason the
//     entry-tree walk exists. A handler reading only `.files` accepts nothing
//     and the page appears to ignore the drop.
//   * `readEntries` returns a BATCH, not a listing. Calling it once looks
//     correct on a small folder and silently truncates a large one at around
//     a hundred entries, which is exactly the size where someone notices a
//     seeding run came up short and cannot tell why.

import { describe, it, expect } from 'vitest';
import {
  filesFromDrop,
  isPdf,
  nameFromFilename,
  seedEmailFor,
} from '../pages/publicApplyDrop';

function pdf(name: string, type = 'application/pdf'): File {
  return new File([new Uint8Array(8)], name, { type });
}

/** A fake FileSystemFileEntry. */
function fileEntry(file: File): unknown {
  return {
    isFile: true,
    isDirectory: false,
    file: (cb: (f: File) => void) => cb(file),
  };
}

/** A fake FileSystemDirectoryEntry whose reader yields `batches` in order. */
function dirEntry(batches: unknown[][]): unknown {
  let i = 0;
  return {
    isFile: false,
    isDirectory: true,
    createReader: () => ({
      readEntries: (cb: (entries: unknown[]) => void) => {
        cb(i < batches.length ? batches[i++] : []);
      },
    }),
  };
}

/** A DataTransfer whose items expose the given entries. */
function transfer(entries: unknown[], plain: File[] = []): DataTransfer {
  const items = [
    ...entries.map((entry) => ({ kind: 'file', webkitGetAsEntry: () => entry })),
    // Items with no entry API at all — the older-Safari path.
    ...plain.map((file) => ({ kind: 'file', getAsFile: () => file })),
  ];
  return { items } as unknown as DataTransfer;
}

describe('isPdf', () => {
  it('accepts a PDF by mime type', () => {
    expect(isPdf(pdf('cv.pdf'))).toBe(true);
  });

  it('falls back to the extension when the system reports no type', () => {
    // Dragged files frequently arrive with an empty or generic type; the
    // server re-checks the bytes either way.
    expect(isPdf(pdf('cv.pdf', ''))).toBe(true);
    expect(isPdf(pdf('cv.pdf', 'application/octet-stream'))).toBe(true);
  });

  it('rejects anything else', () => {
    expect(isPdf(pdf('notes.docx', 'application/msword'))).toBe(false);
    expect(isPdf(pdf('photo.png', 'image/png'))).toBe(false);
  });
});

describe('filesFromDrop', () => {
  it('reads a single dropped file', async () => {
    const files = await filesFromDrop(transfer([fileEntry(pdf('cv.pdf'))]));
    expect(files.map((f) => f.name)).toEqual(['cv.pdf']);
  });

  it('walks a dropped folder', async () => {
    const dt = transfer([
      dirEntry([[fileEntry(pdf('asha.pdf')), fileEntry(pdf('bhavya.pdf'))]]),
    ]);
    const files = await filesFromDrop(dt);
    expect(files.map((f) => f.name)).toEqual(['asha.pdf', 'bhavya.pdf']);
  });

  it('keeps reading until the directory reader is exhausted', async () => {
    // The one that silently truncates: readEntries returns a batch at a time,
    // and stopping after the first yields ~100 of 250 files with no error.
    const dt = transfer([
      dirEntry([
        [fileEntry(pdf('a.pdf'))],
        [fileEntry(pdf('b.pdf'))],
        [fileEntry(pdf('c.pdf'))],
      ]),
    ]);
    const files = await filesFromDrop(dt);
    expect(files.map((f) => f.name)).toEqual(['a.pdf', 'b.pdf', 'c.pdf']);
  });

  it('descends into nested folders', async () => {
    const dt = transfer([
      dirEntry([[dirEntry([[fileEntry(pdf('deep.pdf'))]]), fileEntry(pdf('top.pdf'))]]),
    ]);
    const files = await filesFromDrop(dt);
    expect(files.map((f) => f.name).sort()).toEqual(['deep.pdf', 'top.pdf']);
  });

  it('ignores everything that is not a PDF', async () => {
    const dt = transfer([
      dirEntry([
        [
          fileEntry(pdf('cv.pdf')),
          fileEntry(pdf('.DS_Store', '')),
          fileEntry(pdf('cover.docx', 'application/msword')),
        ],
      ]),
    ]);
    const files = await filesFromDrop(dt);
    expect(files.map((f) => f.name)).toEqual(['cv.pdf']);
  });

  it('still takes plain files when the entry API is unavailable', async () => {
    // Older Safari cannot read a dropped folder at all — taking the files it
    // does give us beats taking nothing.
    const files = await filesFromDrop(transfer([], [pdf('legacy.pdf')]));
    expect(files.map((f) => f.name)).toEqual(['legacy.pdf']);
  });

  it('returns a stable order so a run is reproducible', async () => {
    const dt = transfer([
      dirEntry([[fileEntry(pdf('c.pdf')), fileEntry(pdf('a.pdf')), fileEntry(pdf('b.pdf'))]]),
    ]);
    expect((await filesFromDrop(dt)).map((f) => f.name)).toEqual([
      'a.pdf',
      'b.pdf',
      'c.pdf',
    ]);
  });

  it('handles an empty drop without throwing', async () => {
    expect(await filesFromDrop(transfer([]))).toEqual([]);
  });
});

describe('nameFromFilename', () => {
  it('reads a name out of a typical CV filename', () => {
    expect(nameFromFilename('priya_sharma_resume_v2.pdf')).toBe('Priya Sharma');
    expect(nameFromFilename('Rahul-Verma-CV.pdf')).toBe('Rahul Verma');
  });

  it('falls back rather than producing an empty name', () => {
    // full_name has a 2-character minimum server-side; an empty string would
    // 422 the whole seeding run on one badly named file.
    expect(nameFromFilename('cv.pdf')).toBe('Candidate');
    expect(nameFromFilename('1234.pdf')).toBe('Candidate');
  });
});

describe('seedEmailFor', () => {
  it('always lands in a domain that cannot reach a real person', () => {
    // Committing a decision on a seeded candidate does try to send mail.
    // example.com is reserved by RFC 2606 and never accepts mail — and,
    // unlike .invalid, it passes the server's EmailStr validation. Using
    // .invalid here 422'd every seeded application.
    expect(seedEmailFor('asha_rao.pdf')).toMatch(/@seed\.example\.com$/);
  });

  it('passes the shape the server will validate', () => {
    // Deliberately mirrors what pydantic EmailStr accepts: a local part, one
    // @, and a registrable domain that is not a special-use TLD.
    const address = seedEmailFor('asha_rao.pdf');
    expect(address).toMatch(/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/);
    expect(address).not.toMatch(/\.(invalid|test|localhost)$/);
  });

  it('is unique per call, so two similar filenames do not collide', () => {
    // Colliding would trip one-application-per-opening and report the second
    // as a duplicate rather than seeding it.
    const a = seedEmailFor('resume.pdf');
    const b = seedEmailFor('resume.pdf');
    expect(a).not.toBe(b);
  });

  it('produces a syntactically valid address from an awkward filename', () => {
    const address = seedEmailFor('  Ünïcødé (final) [v3].pdf  ');
    expect(address).toMatch(/^[a-z0-9.]+@seed\.example\.com$/);
    expect(address).not.toContain('..');
  });
});
