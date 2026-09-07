// questions.ts — the questions an opening asks its applicants.
//
// Authoring side. The candidate side lives in `publicApply.ts`, which has no
// session; this one is HR-only and goes through the authenticated client.
//
// The rule worth knowing before wiring a form to this: once anybody has
// answered a question, its wording and its type are frozen. Editing "Do you
// have a work visa?" into "Do you need visa sponsorship?" would invert the
// meaning of every stored yes with nothing in the data recording it, so the
// server refuses with a 409. `answer_count` is on every question precisely so
// the console can explain that BEFORE somebody starts typing.

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from './client';

export type QuestionKind =
  | 'short_text'
  | 'long_text'
  | 'number'
  | 'single_choice'
  | 'multi_choice'
  | 'yes_no';

export const QUESTION_KIND_LABELS: Record<QuestionKind, string> = {
  short_text: 'Short answer',
  long_text: 'Long answer',
  number: 'Number',
  single_choice: 'Choose one',
  multi_choice: 'Choose several',
  yes_no: 'Yes / No',
};

/** Kinds that need a list of options, and are refused without at least two. */
export const CHOICE_KINDS: QuestionKind[] = ['single_choice', 'multi_choice'];

export interface ApplicationQuestion {
  id: string;
  position: number;
  prompt: string;
  kind: QuestionKind;
  help_text: string | null;
  required: boolean;
  options: string[];
  /** How many applicants have answered. Non-zero means the wording is frozen. */
  answer_count: number;
}

export interface QuestionInput {
  prompt: string;
  kind: QuestionKind;
  required?: boolean;
  help_text?: string | null;
  options?: string[];
}

/** Whether this question can still be reworded. */
export function isFrozen(question: ApplicationQuestion): boolean {
  return question.answer_count > 0;
}

export function listQuestions(requisitionId: string): Promise<ApplicationQuestion[]> {
  return apiGet<ApplicationQuestion[]>(`/hr/requisitions/${requisitionId}/questions`);
}

/**
 * Add a question. Returns the whole list, because adding changes positions and
 * a client that has to re-fetch to find out what it now looks like will
 * sometimes forget to.
 */
export function addQuestion(
  requisitionId: string,
  body: QuestionInput,
): Promise<ApplicationQuestion[]> {
  return apiPost<ApplicationQuestion[]>(
    `/hr/requisitions/${requisitionId}/questions`,
    body,
  );
}

export function updateQuestion(
  questionId: string,
  body: Partial<Omit<QuestionInput, 'kind'>>,
): Promise<ApplicationQuestion[]> {
  return apiPatch<ApplicationQuestion[]>(`/hr/questions/${questionId}`, body);
}

/** Stop asking it. The answers already given stay readable on their applications. */
export function retireQuestion(questionId: string): Promise<void> {
  return apiDelete<void>(`/hr/questions/${questionId}`);
}

export function reorderQuestions(
  requisitionId: string,
  questionIds: string[],
): Promise<ApplicationQuestion[]> {
  return apiPut<ApplicationQuestion[]>(
    `/hr/requisitions/${requisitionId}/questions/order`,
    { question_ids: questionIds },
  );
}

/** One applicant's answers, as HR reads them. */
export interface ApplicationAnswer {
  question_id: string;
  prompt: string;
  kind: QuestionKind;
  /** True when the question is no longer asked. The answer still stands. */
  retired: boolean;
  answer: string | number | boolean | string[];
}

export function listAnswers(enrolmentId: string): Promise<ApplicationAnswer[]> {
  return apiGet<ApplicationAnswer[]>(`/hr/enrolments/${enrolmentId}/answers`);
}
