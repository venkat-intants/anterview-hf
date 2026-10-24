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

/** What a time-limit input holds: blank (no limit), a whole number of
 *  minutes (as seconds), or something that is neither. */
export type MinutesInput =
  | { kind: 'none' }
  | { kind: 'limit'; seconds: number }
  | { kind: 'invalid' };

/** The message for a time limit that is not blank and not a positive number. */
export const INVALID_MINUTES_MESSAGE =
  'Enter a time limit of 1 minute or more, or leave it blank for no limit.';

/** Read a time-limit input. Only blank means "no limit": 0, a negative number
 *  or anything that rounds below one minute is invalid, so typing 0 can never
 *  quietly remove a limit (re-audit). */
export function parseMinutes(raw: string): MinutesInput {
  const trimmed = raw.trim();
  if (!trimmed) return { kind: 'none' };
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return { kind: 'invalid' };
  const minutes = Math.round(n);
  return minutes >= 1 ? { kind: 'limit', seconds: minutes * 60 } : { kind: 'invalid' };
}

/** Whole minutes from a number input as seconds, or null for no limit. A value
 *  parseMinutes calls invalid is null too, never NaN or 0 seconds; a caller
 *  that can say so should use parseMinutes and refuse it instead. */
export function minutesToSeconds(raw: string): number | null {
  const parsed = parseMinutes(raw);
  return parsed.kind === 'limit' ? parsed.seconds : null;
}

/** A pass mark from a number input. 0 is a valid pass mark, so this does not
 *  use `Number(x) || 60`, which turned 0 into 60 (audit E16). */
export function parseThreshold(raw: string, fallback: number): number {
  const n = Number(raw);
  if (raw.trim() === '' || !Number.isFinite(n)) return fallback;
  return Math.min(100, Math.max(0, Math.round(n)));
}
