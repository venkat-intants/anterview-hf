// applicationStage — the candidate-facing stage words, translated.
//
// The server sends English: a `stage` label ("Under review") and, on a newer
// server, a `stage_code` ("under_review") in the same candidate vocabulary —
// never the raw internal status. This module owns the translation:
//
//   1. `stage_code`, when present and one of the six we know;
//   2. otherwise the English label, mapped back to its code;
//   3. otherwise NOTHING — the caller prints the server's own string.
//
// Falling through is deliberate. A stage this build has never heard of must show
// the server's words rather than nothing, an invented translation, or a crash.

import type { TFunction } from 'i18next';
import type { TagTone } from '@/design/components/primitives';

export const STAGE_CODES = [
  'received',
  'shortlisted',
  'interview_complete',
  'under_review',
  'selected',
  'not_progressing',
] as const;

export type KnownStageCode = (typeof STAGE_CODES)[number];

/** The six English labels `candidate_applications.py` sends today. */
const LABEL_TO_CODE: Readonly<Record<string, KnownStageCode>> = {
  'Application received': 'received',
  Shortlisted: 'shortlisted',
  'Interview complete': 'interview_complete',
  'Under review': 'under_review',
  Selected: 'selected',
  'Not progressing': 'not_progressing',
};

export const STAGE_TONE: Readonly<Record<KnownStageCode, TagTone>> = {
  received: 'neutral',
  shortlisted: 'electric',
  interview_complete: 'lavender',
  under_review: 'amber',
  selected: 'forest',
  not_progressing: 'ember',
};

function isKnown(code: string | null | undefined): code is KnownStageCode {
  return typeof code === 'string' && (STAGE_CODES as readonly string[]).includes(code);
}

/** The known code for a stage, or null when this build does not recognise it. */
export function knownStageCode(item: {
  stage: string;
  stage_code?: string | null;
}): KnownStageCode | null {
  if (isKnown(item.stage_code)) return item.stage_code;
  return LABEL_TO_CODE[item.stage] ?? null;
}

/** The stage in the reader's language, or the server's own string when unknown. */
export function stageText(
  t: TFunction,
  item: { stage: string; stage_code?: string | null },
): string {
  const code = knownStageCode(item);
  return code ? t(`stage.${code}`) : item.stage;
}

/**
 * The "what happens next" sentence for a stage with nothing waiting on the
 * candidate, translated; the server's own sentence when the stage is unknown.
 */
export function nextStepText(
  t: TFunction,
  item: { stage: string; stage_code?: string | null; next_step: string },
): string {
  const code = knownStageCode(item);
  return code ? t(`stage.next.${code}`) : item.next_step;
}

export function stageTone(item: { stage: string; stage_code?: string | null }): TagTone {
  const code = knownStageCode(item);
  return code ? STAGE_TONE[code] : 'neutral';
}
