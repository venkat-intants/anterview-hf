// Exam progress that survives a reload (audit CA-M2).
//
// Phase, answers, code and section position used to live only in component
// state. A reload put the candidate back on the intro; "Start exam" resumed
// the same server attempt (deadline kept) but blank, and every section timer
// started again from full. This keeps that progress in this tab's
// sessionStorage, the same place and lifetime as the exam token itself, keyed
// by the round so a different round never picks it up, and bound to the
// attempt id so a new attempt never inherits an old one's answers.
//
// It is a convenience, not a record: the server holds the attempt. Storage can
// be blocked, so every access is guarded and a failure only means the old
// blank-resume behaviour.

import type { CodingAnswer } from '@/api/publicExam';

const PREFIX = 'intants:exam-progress:';

export interface ExamProgress {
  attemptId: string;
  answers: Record<string, number>;
  submissions: Record<string, CodingAnswer>;
  sectionIndex: number;
  /** When each section was first entered, epoch ms. Section timers run from it. */
  sectionStartedAt: Record<string, number>;
}

export function progressKey(examId: string, roundId: string): string {
  return `${PREFIX}${examId}:${roundId}`;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function parse(raw: string | null): ExamProgress | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(v) || typeof v.attemptId !== 'string' || typeof v.sectionIndex !== 'number') {
    return null;
  }
  const answers: Record<string, number> = {};
  if (isRecord(v.answers)) {
    for (const [k, a] of Object.entries(v.answers)) if (typeof a === 'number') answers[k] = a;
  }
  const submissions: Record<string, CodingAnswer> = {};
  if (isRecord(v.submissions)) {
    for (const [k, s] of Object.entries(v.submissions)) {
      if (isRecord(s) && typeof s.language === 'string' && typeof s.source === 'string') {
        submissions[k] = { language: s.language, source: s.source };
      }
    }
  }
  const sectionStartedAt: Record<string, number> = {};
  if (isRecord(v.sectionStartedAt)) {
    for (const [k, at] of Object.entries(v.sectionStartedAt)) {
      if (typeof at === 'number' && Number.isFinite(at)) sectionStartedAt[k] = at;
    }
  }
  return {
    attemptId: v.attemptId,
    answers,
    submissions,
    sectionIndex: Math.max(0, Math.floor(v.sectionIndex)),
    sectionStartedAt,
  };
}

export function loadProgress(key: string): ExamProgress | null {
  try {
    return parse(sessionStorage.getItem(key));
  } catch {
    return null;
  }
}

export function saveProgress(key: string, progress: ExamProgress): void {
  try {
    sessionStorage.setItem(key, JSON.stringify(progress));
  } catch {
    /* blocked or full: a reload then resumes blank, as before */
  }
}

export function clearProgress(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* blocked: it was never written either */
  }
}
