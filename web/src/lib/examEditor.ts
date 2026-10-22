// examEditor: small pieces the exam editor and its lazily loaded coding
// section share, kept here so the lazy chunk does not import the editor page.
//
// The editor header shows "Saving…/Saved" for the edits made ON the editor.
// It used `useIsMutating()` with no filter, which counted every mutation in the
// app (a Save-to-bank, an unrelated panel), so it could read "Saving…" while
// nothing in the exam was being saved (audit E22). Every edit to the exam's
// own content or settings carries this key; the header counts only those.

import type { MutationKey } from '@tanstack/react-query';

export function examEditorMutationKey(examId: string): MutationKey {
  return ['exam-editor', examId];
}

/** Whole minutes from a number input, or null for blank ("no limit"). A value
 *  that is not a positive number is null too, never NaN seconds. */
export function minutesToSeconds(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) && n > 0 ? Math.round(n) * 60 : null;
}

/** A pass mark from a number input. 0 is a valid pass mark, so this does not
 *  use `Number(x) || 60`, which turned 0 into 60 (audit E16). */
export function parseThreshold(raw: string, fallback: number): number {
  const n = Number(raw);
  if (raw.trim() === '' || !Number.isFinite(n)) return fallback;
  return Math.min(100, Math.max(0, Math.round(n)));
}
