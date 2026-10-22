// Whether screening questions have been answered. Shared by the apply form and
// the saved-application page, which must agree on what "answered" means: the
// server refuses a submission with a required question left blank either way.

import type { AnswerValue, PostingQuestion } from '@/api/publicApply';

/** Whether an answer counts as given. Blank text and an empty pick do not. */
export function isAnswered(value: AnswerValue | undefined | null): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return value.trim() !== '';
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

/** Whether every required question has an answer. */
export function requiredAnswered(
  questions: readonly PostingQuestion[],
  answers: Record<string, AnswerValue>,
): boolean {
  return questions.filter((q) => q.required).every((q) => isAnswered(answers[q.id]));
}
