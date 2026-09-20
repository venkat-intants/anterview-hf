// workflowReview.ts — PH4-O6 (review & approval) and PH4-O2 (dry run).
//
// draft ──submit──▶ in_review ──approve──▶ approved ──publish──▶ published
//   ▲    ▲             │   │                  │
//   │    │             │   └─request changes─▶ changes_requested ─submit─┐
//   │    └──withdraw───┘                      │                          │
//   └─────────────────reopen──────────────────┘◀─────────────────────────┘
//
// HR managers author a version, submit it, withdraw it, reopen an approved
// one and publish an approved one. The company's super admin approves it or
// asks for changes (D4-2) — a different account from the one that wrote it.
//
// A dry run walks synthetic candidates through the actual routing logic
// (`route_after_result`), so it tests what will run rather than a second copy
// of it. It writes only its own result — no applicant, enrolment, invite or
// notification — so a passing dry run publishes nothing.
//
// Field names match services/data_gateway/app/routers/workflow_ops.py EXACTLY.

import { apiGet, apiPost } from './client';
import { pathId } from './pathId';
import type { ReviewStatus, ValidationReport } from './workflows';
import type { StageSetting } from './stageSla';

// ── Review state ─────────────────────────────────────────────────────────

export type ReviewAction =
  | 'submitted'
  | 'withdrawn'
  | 'approved'
  | 'changes_requested'
  | 'reopened';

export interface ReviewHistoryEntry {
  action: ReviewAction;
  note: string | null;
  actor_name: string | null;
  at: string;
  simulation_id: string | null;
}

export interface ReviewState {
  review_status: ReviewStatus;
  submitted_at: string | null;
  submitted_by_name: string | null;
  reviewed_at: string | null;
  reviewed_by_name: string | null;
  note: string | null;
  history: ReviewHistoryEntry[];
}

export function getReview(workflowId: string): Promise<ReviewState> {
  return apiGet<ReviewState>(`/hr/workflows/${pathId(workflowId)}/review`);
}

// ── Dry run (O2) ─────────────────────────────────────────────────────────

export type SimulationSeverity = 'error' | 'warning';
export type SimulationRoundState = 'ok' | 'warning' | 'error';
export type SimulationBranch = 'pass' | 'fast_track' | 'fail';
// 'waiting': passed, and stays on the round until a person moves them —
// what the runner does when rounds do not advance automatically.
export type SimulationEnd = 'decision' | 'held' | 'waiting' | 'error';

export interface SimulationFinding {
  severity: SimulationSeverity;
  message: string;
  round_id: string | null;
}

export interface SimulationRound {
  round_id: string;
  title: string;
  kind: string;
  position: number;
  state: SimulationRoundState;
  findings: SimulationFinding[];
  branches: { branch: SimulationBranch; round_id: string }[];
}

export interface SimulationStepNext {
  kind: 'round' | 'complete' | 'hold' | 'person';
  round_id?: string | null;
  round_title?: string | null;
}

export interface SimulationStep {
  round_id: string;
  round_title: string | null;
  kind?: string;
  outcome?: SimulationBranch;
  simulated_percent: number | null;
  branch?: SimulationBranch;
  next?: SimulationStepNext;
  error?: string;
}

export interface SimulationScenario {
  id: string;
  candidate: string;
  description: string;
  end: SimulationEnd;
  steps: SimulationStep[];
}

export interface SimulationResult {
  simulation_id: string;
  workflow_id: string;
  version: number;
  fingerprint: string;
  created_at: string;
  /** True when the workflow changed since this run — rerun before trusting it. */
  stale: boolean;
  run_by_name?: string | null;
  status: 'passed' | 'warnings' | 'failed';
  errors: number;
  warnings: number;
  rounds: SimulationRound[];
  workflow_findings: SimulationFinding[];
  scenarios: SimulationScenario[];
}

export function runSimulation(workflowId: string): Promise<SimulationResult> {
  return apiPost<SimulationResult>(`/hr/workflows/${pathId(workflowId)}/simulate`, {});
}

export function getSimulation(workflowId: string): Promise<SimulationResult | null> {
  return apiGet<SimulationResult | null>(`/hr/workflows/${pathId(workflowId)}/simulation`);
}

// ── HR — submit / withdraw / reopen ─────────────────────────────────────────

export interface SubmitReviewResult {
  review_status: ReviewStatus;
  simulation: SimulationResult;
  approvers: number;
  review: ReviewState;
}

export function submitForReview(workflowId: string, note?: string): Promise<SubmitReviewResult> {
  return apiPost<SubmitReviewResult>(`/hr/workflows/${pathId(workflowId)}/submit-review`, {
    note: note?.trim() || null,
  });
}

export function withdrawReview(workflowId: string): Promise<ReviewState> {
  return apiPost<ReviewState>(`/hr/workflows/${pathId(workflowId)}/withdraw-review`, {});
}

export function reopenForEdits(workflowId: string, note?: string): Promise<ReviewState> {
  return apiPost<ReviewState>(`/hr/workflows/${pathId(workflowId)}/reopen`, {
    note: note?.trim() || null,
  });
}

/**
 * The 422 body a failed submit-for-review carries: a message plus EITHER a
 * ValidationReport or a SimulationResult, never both. `ApiError` keeps only
 * `detail` in its parsed form, so this pulls it back out — mirrors
 * `validationFromError` in api/workflows.ts. Null is a normal outcome for any
 * other error (a plain string detail, a network failure).
 */
export interface ReviewErrorDetail {
  message: string;
  validation?: ValidationReport;
  simulation?: SimulationResult;
}

export function reviewErrorDetail(err: unknown): ReviewErrorDetail | null {
  const detail = (err as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return detail as ReviewErrorDetail;
  }
  return null;
}

// ── Super admin — the review queue (D4-2) ───────────────────────────────────

export interface PendingWorkflowReview {
  workflow_id: string;
  version: number;
  requisition_id: string;
  opening_title: string;
  submitted_at: string | null;
  submitted_by_name: string | null;
  note: string | null;
  rounds: number;
}

/** Oldest first — a waiting list, sorted any other way buries who waited longest. */
export function listPendingWorkflowReviews(): Promise<PendingWorkflowReview[]> {
  return apiGet<PendingWorkflowReview[]>('/admin/workflow-reviews');
}

export interface ReviewRound {
  round_id: string;
  position: number;
  title: string;
  kind: string;
  pass_threshold: number | null;
  deadline_days: number;
  /** Round TITLES, or null — the branch targets, worded for a reviewer. */
  on_pass: string | null;
  on_fail: string | null;
  fast_track_min_percent: number | null;
  on_fast_track: string | null;
  criteria: { name: string; weight: number }[];
}

export interface WorkflowReviewDetail {
  workflow_id: string;
  requisition_id: string;
  opening_title: string;
  version: number;
  status: string;
  settings: Record<string, unknown>;
  rounds: ReviewRound[];
  validation: ValidationReport;
  simulation: SimulationResult | null;
  stages: StageSetting[];
  review: ReviewState;
}

export function getWorkflowReviewDetail(workflowId: string): Promise<WorkflowReviewDetail> {
  return apiGet<WorkflowReviewDetail>(`/admin/workflow-reviews/${pathId(workflowId)}`);
}

export function approveWorkflowReview(workflowId: string, note?: string): Promise<ReviewState> {
  return apiPost<ReviewState>(`/admin/workflow-reviews/${pathId(workflowId)}/approve`, {
    note: note?.trim() || null,
  });
}

/** The note is required server-side (>= 10 characters after trimming). */
export function requestWorkflowChanges(workflowId: string, note: string): Promise<ReviewState> {
  return apiPost<ReviewState>(`/admin/workflow-reviews/${pathId(workflowId)}/request-changes`, {
    note: note.trim(),
  });
}
