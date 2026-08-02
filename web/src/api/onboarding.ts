// onboarding.ts — self-serve onboarding + the personal practice plan.
//
// The self-serve journey is a student practising on their own, as opposed to
// an applicant an HR manager invited. Onboarding runs once; afterwards their
// dashboard is a practice plan built from the role engine's competencies for
// the role they said they're aiming for.
//
// Endpoints live under /users/me/* — the same prefix as the resume endpoints,
// and one Caddy already routes to data_gateway.

import { apiGet, apiPost } from './client';

/** Why they're practising. Shapes the dashboard's nudge copy. */
export type OnboardingGoal =
  | 'campus_placement'
  | 'first_job'
  | 'switching_field'
  | 'interview_soon'
  | 'general_practice';

export type TargetLevel = 'entry' | 'mid' | 'senior';

export interface OnboardingStatus {
  /** False for HR/admin — never route them to the wizard. */
  applicable: boolean;
  /** True once completed OR skipped, so the wizard interrupts exactly once. */
  seen: boolean;
  completed: boolean;
  skipped: boolean;
  full_name: string | null;
  goal: OnboardingGoal | null;
  target_role: string | null;
  target_level: TargetLevel | null;
  preferred_language: string;
}

export interface OnboardingSubmit {
  full_name?: string | null;
  goal?: OnboardingGoal | null;
  target_role: string;
  target_level: TargetLevel;
  preferred_language: string;
}

export interface CompetencyProgress {
  id: string;
  name: string;
  kind: string;
  /** Share of the interview this competency gets for this role. */
  weight: number;
  /** What "adequate" looks like — the concrete target. */
  target: string;
  /** Mean score 0-10, or null when never probed. NOT zero — different fact. */
  score: number | null;
  attempts: number;
  /** Latest score minus the previous one; null with fewer than two. */
  trend: number | null;
}

export interface PracticePlan {
  /** False when no target role is set — show the nudge card instead. */
  ready: boolean;
  target_role: string | null;
  target_level: TargetLevel | null;
  domain_family: string | null;
  domain_label: string | null;
  what_this_person_does: string | null;
  competencies: CompetencyProgress[];
  /** 0-100, or null until the first mock interview is scored. */
  readiness: number | null;
  interviews_completed: number;
  last_interview_at: string | null;
  focus_competency_id: string | null;
  focus_competency_name: string | null;
  never_probed: string[];
}

export interface NextInterviewShape {
  ready: boolean;
  target_role?: string;
  turns: { turn: number; kind: string; competency: string | null }[];
}

export function getOnboardingStatus(): Promise<OnboardingStatus> {
  return apiGet<OnboardingStatus>('/users/me/onboarding');
}

export function submitOnboarding(body: OnboardingSubmit): Promise<OnboardingStatus> {
  return apiPost<OnboardingStatus>('/users/me/onboarding', body);
}

export function skipOnboarding(): Promise<OnboardingStatus> {
  return apiPost<OnboardingStatus>('/users/me/onboarding/skip', {});
}

export function getPracticePlan(): Promise<PracticePlan> {
  return apiGet<PracticePlan>('/users/me/practice-plan');
}

/**
 * What the plan WOULD look like for a role, without saving.
 *
 * Powers the live "→ Mechanical & Manufacturing ✓" confirmation while the
 * student types, so they can tell instantly whether we understood the job.
 */
export function previewPracticePlan(
  targetRole: string,
  targetLevel: TargetLevel,
): Promise<PracticePlan> {
  const q = new URLSearchParams({ target_role: targetRole, target_level: targetLevel });
  return apiGet<PracticePlan>(`/users/me/practice-plan/preview?${q.toString()}`);
}

export function getNextInterviewShape(): Promise<NextInterviewShape> {
  return apiGet<NextInterviewShape>('/users/me/practice-plan/next-interview');
}

/** Wizard copy for the goal step. Order is the order shown. */
export const GOAL_OPTIONS: { value: OnboardingGoal; label: string; hint: string }[] = [
  {
    value: 'campus_placement',
    label: 'Preparing for campus placement',
    hint: 'Company visits, aptitude rounds, group discussions',
  },
  { value: 'first_job', label: 'Looking for my first job', hint: 'Fresh out of college' },
  {
    value: 'switching_field',
    label: 'Switching to a new field',
    hint: 'Moving into a different kind of work',
  },
  {
    value: 'interview_soon',
    label: 'I have an interview coming up',
    hint: 'Something specific, soon',
  },
  { value: 'general_practice', label: 'Just practising', hint: 'Getting comfortable talking' },
];

export const LEVEL_OPTIONS: { value: TargetLevel; label: string; hint: string }[] = [
  { value: 'entry', label: 'Fresher', hint: 'No professional experience yet' },
  { value: 'mid', label: '1–3 years', hint: 'Some real work behind me' },
  { value: 'senior', label: '3+ years', hint: 'Experienced' },
];

export const LANGUAGE_OPTIONS: { value: string; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'हिन्दी' },
  { value: 'te', label: 'తెలుగు' },
];
