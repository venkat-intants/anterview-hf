// The opening's setup journey, as data (UX round 3, stream A).
//
// An opening goes live through five stages. This file turns what the server
// holds into the stages `JourneyRail` draws, and nothing else: no component, no
// fetching, so every rule is table-testable.
//
// THE RULE A TICK FOLLOWS
// `done` means the server holds the artefact, never that fields were filled:
//
//   1 Role and posting      JdHistory.published_version_id is set
//   2 Hiring process        a workflow version is published
//   3 Budget and approval   approval_status === 'approved'
//   4 Go live               public_apply_enabled, status open, and a live workflow
//   5 Preboarding documents never ticked (zero requirements is a legitimate
//                           choice), so it is `optional` whatever its count
//
// Stages 2 and 3 are parallel on the server (publishing a workflow does not read
// the opening's approval), so neither blocks the other. Stage 4 needs both and is
// `blocked` with a sentence naming what is missing until they are done.
//
// Sub-steps under stage 2 describe the version being worked on (the draft if
// there is one, else the live one) and reuse `stepStates()` for the review part.

import type { ApprovalStatus, BudgetBasis, BudgetPeriod, RequisitionStatus } from '@/api/requisitions';
import type { JourneyStage, JourneySubstep, SubstepState } from '@/design/components/journey-rail';
import { stepStates, type StepState } from '@/lib/workflowLifecycle';

export type StageKey = 'role' | 'process' | 'approval' | 'golive' | 'documents';

export const STAGE_ORDER: readonly StageKey[] = ['role', 'process', 'approval', 'golive', 'documents'];

/** What the stage's primary action does: go somewhere, or open the stage's panel. */
export interface StageAction {
  label: string;
  /** A route, or null to open the stage's own panel on the hub. */
  to: string | null;
}

export interface OpeningStage extends JourneyStage {
  key: StageKey;
  /** The facts for this stage are still loading; nothing may be inferred from it. */
  loading?: boolean;
  /** Present when the stage is waiting on the user (`todo` or `active`). */
  action?: StageAction;
}

/** The JD facts the first stage reads. Build it with `jdFacts()`. */
export interface JdFacts {
  publishedVersionId: string | null;
  publishedVersion: number | null;
  draftVersion: number | null;
}

export interface JdHistoryLike {
  published_version_id: string | null;
  versions: { id: string; version: number; status: 'draft' | 'published' | 'archived' }[];
}

export function jdFacts(h: JdHistoryLike): JdFacts {
  const published =
    h.versions.find((v) => v.id === h.published_version_id) ??
    h.versions.find((v) => v.status === 'published') ??
    null;
  const draft = h.versions.find((v) => v.status === 'draft') ?? null;
  return {
    publishedVersionId: h.published_version_id,
    publishedVersion: h.published_version_id ? (published?.version ?? null) : null,
    draftVersion: draft?.version ?? null,
  };
}

/** The workflow version being worked on, reduced to what the rail reads. */
export interface WorkflowFacts {
  version: number;
  status: 'draft' | 'published' | 'archived';
  reviewStatus: 'draft' | 'in_review' | 'changes_requested' | 'approved';
  rounds: { kind: string; needsQuestions: boolean; examRoundId: string | null }[];
  /** A dry run exists for the current content (the latest simulation is not stale). */
  simulated: boolean;
  publishable: boolean;
}

const EXAM_BACKED = ['mcq', 'coding'];

export interface OpeningSetupInput {
  requisitionId: string;
  /** `readonly` is the super admin's view, which can read only the dashboard. */
  viewer?: 'hr' | 'readonly';
  status: RequisitionStatus;
  publicApplyEnabled: boolean;
  approvalStatus: ApprovalStatus;
  submittedByName?: string | null;
  submittedAt?: string | null;
  decidedByName?: string | null;
  decidedAt?: string | null;
  approvalNote?: string | null;
  budget?: {
    amount: number | null | undefined;
    currency: string | null | undefined;
    basis: BudgetBasis | null | undefined;
    period: BudgetPeriod | null | undefined;
  };
  /** From the dashboard. */
  publishedWorkflowVersion: number | null;
  draftWorkflowVersion: number | null;
  hasPublishedWorkflow: boolean;
  /** Undefined until the workflow queries answer. */
  workflow?: WorkflowFacts | null;
  /** Undefined until JD history answers. */
  jd?: JdFacts;
  /** Undefined until the schedule answers; null means nothing is scheduled. */
  publishAt?: string | null;
  /** Undefined until the document requirements answer. */
  documentCount?: number;
}

export const APPROVAL_LABEL: Record<ApprovalStatus, string> = {
  draft: 'Not submitted',
  pending_approval: 'Awaiting approval',
  approved: 'Approved',
  rejected: 'Changes requested',
};

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

const BASIS: Record<BudgetBasis, string> = { per_hire: 'per hire', total: 'total' };
const PERIOD: Record<BudgetPeriod, string> = { annual: 'annual', monthly: 'monthly', one_time: 'one-time' };

/** "INR 18,00,000 per hire, annual", or null when no budget is recorded. */
export function budgetLine(b: OpeningSetupInput['budget']): string | null {
  if (!b || b.amount === null || b.amount === undefined) return null;
  const parts = [`${b.currency ?? ''} ${b.amount.toLocaleString('en-IN')}`.trim()];
  if (b.basis) parts.push(BASIS[b.basis]);
  const base = parts.join(' ');
  return b.period ? `${base}, ${PERIOD[b.period]}` : base;
}

function clip(s: string, n = 80): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function sub(state: StepState): SubstepState {
  return state === 'current' ? 'active' : state;
}

/* ── Stage 1 ────────────────────────────────────────────────────────────── */

function roleStage(i: OpeningSetupInput): OpeningStage {
  const base = { key: 'role', title: 'Role and posting' } as const;
  if (!i.jd) {
    return { ...base, state: 'todo', fact: 'Checking the job description…', loading: true };
  }
  const { publishedVersion, publishedVersionId, draftVersion } = i.jd;
  if (publishedVersionId !== null) {
    const v = publishedVersion !== null ? `JD v${publishedVersion} published` : 'JD published';
    return {
      ...base,
      state: 'done',
      fact: draftVersion !== null ? `${v}, draft v${draftVersion} unpublished` : v,
    };
  }
  if (draftVersion !== null) {
    return {
      ...base,
      state: 'active',
      fact: `No JD published, draft v${draftVersion} unpublished`,
      action: { label: 'Publish the job description', to: null },
    };
  }
  return {
    ...base,
    state: 'todo',
    fact: 'No JD published',
    action: { label: 'Write the job description', to: null },
  };
}

/* ── Stage 2 ────────────────────────────────────────────────────────────── */

/** "Exams attached" for the version in focus, from the rounds' own fields. */
export function examProgress(rounds: WorkflowFacts['rounds']): { total: number; attached: number } {
  const backed = rounds.filter((r) => EXAM_BACKED.includes(r.kind));
  return {
    total: backed.length,
    attached: backed.filter((r) => !r.needsQuestions && r.examRoundId !== null).length,
  };
}

function processSubsteps(i: OpeningSetupInput): JourneySubstep[] | undefined {
  if (i.viewer === 'readonly') return undefined;
  const none = i.publishedWorkflowVersion === null && i.draftWorkflowVersion === null;
  if (none) {
    return [
      { key: 'rounds', label: 'Rounds', state: 'active' },
      { key: 'exams', label: 'Exams attached', state: 'todo' },
      { key: 'dryRun', label: 'Dry run', state: 'todo' },
      { key: 'submitted', label: 'Submitted', state: 'todo' },
      { key: 'approved', label: 'Approved', state: 'todo' },
      { key: 'published', label: 'Published', state: 'todo' },
    ];
  }
  const w = i.workflow;
  if (!w) return undefined; // something exists but its detail has not loaded yet
  const hasRounds = w.rounds.length > 0;
  const exams = examProgress(w.rounds);
  const lifecycle = stepStates({
    status: w.status,
    reviewStatus: w.reviewStatus,
    simulated: w.simulated,
    publishable: w.publishable,
  });
  // A draft with no rounds has nothing to dry-run, submit or approve yet.
  const gate = (s: StepState): SubstepState => (hasRounds ? sub(s) : 'todo');
  let examState: SubstepState;
  let examLabel: string;
  if (!hasRounds) {
    examState = 'todo';
    examLabel = 'Exams attached';
  } else if (exams.total === 0) {
    examState = 'done';
    examLabel = 'No round needs an exam';
  } else {
    examState = exams.attached === exams.total ? 'done' : 'active';
    examLabel = `Exams attached (${exams.attached} of ${exams.total})`;
  }
  return [
    { key: 'rounds', label: hasRounds ? `Rounds (${w.rounds.length})` : 'Rounds', state: hasRounds ? 'done' : 'active' },
    { key: 'exams', label: examLabel, state: examState },
    { key: 'dryRun', label: 'Dry run', state: gate(lifecycle.dryRun) },
    { key: 'submitted', label: 'Submitted', state: gate(lifecycle.review) },
    { key: 'approved', label: 'Approved', state: gate(lifecycle.approved) },
    { key: 'published', label: 'Published', state: hasRounds ? sub(lifecycle.publish) : 'todo' },
  ];
}

const WORKFLOW_REVIEW_WORD: Record<WorkflowFacts['reviewStatus'], string> = {
  draft: 'draft',
  in_review: 'in review',
  changes_requested: 'changes requested',
  approved: 'approved, not yet published',
};

function processStage(i: OpeningSetupInput): OpeningStage {
  const to = `/hr/requisitions/${i.requisitionId}/workflow`;
  const published = i.publishedWorkflowVersion;
  const draft = i.draftWorkflowVersion;
  const w = i.workflow;
  const substeps = processSubsteps(i);
  const draftWord = draft !== null && w && w.status === 'draft' ? ` (${WORKFLOW_REVIEW_WORD[w.reviewStatus]})` : '';
  const draftText = draft !== null ? `v${draft} draft${draftWord}` : null;

  if (i.hasPublishedWorkflow) {
    return {
      key: 'process',
      title: 'Hiring process',
      state: 'done',
      fact: [published !== null ? `v${published} live` : 'Workflow live', draftText].filter(Boolean).join(' · '),
      substeps,
    };
  }
  if (draft !== null) {
    if (w && w.status === 'draft' && w.reviewStatus === 'in_review') {
      return {
        key: 'process',
        title: 'Hiring process',
        state: 'awaiting',
        fact: `${draftText ?? 'Draft'}, waiting for a reviewer`,
        substeps,
      };
    }
    return {
      key: 'process',
      title: 'Hiring process',
      state: 'active',
      fact: `Nothing live yet · ${draftText ?? 'draft in progress'}`,
      substeps,
      action: {
        label: w && w.reviewStatus === 'approved' ? 'Publish the workflow' : 'Continue the hiring process',
        to,
      },
    };
  }
  return {
    key: 'process',
    title: 'Hiring process',
    state: 'todo',
    fact: 'No workflow yet',
    substeps,
    action: { label: 'Build the hiring process', to },
  };
}

/* ── Stage 3 ────────────────────────────────────────────────────────────── */

function approvalStage(i: OpeningSetupInput): OpeningStage {
  const budget = budgetLine(i.budget);
  const base = { key: 'approval', title: 'Budget and approval' } as const;
  switch (i.approvalStatus) {
    case 'approved':
      return {
        ...base,
        state: 'done',
        fact: budget ? `Approved · ${budget}` : 'Approved',
        actor: i.decidedByName ? `Approved by ${i.decidedByName}` : undefined,
        at: shortDate(i.decidedAt) || undefined,
      };
    case 'pending_approval':
      return {
        ...base,
        state: 'awaiting',
        fact: 'Waiting for your company admin',
        actor: i.submittedByName ? `Submitted by ${i.submittedByName}` : undefined,
        at: shortDate(i.submittedAt) || undefined,
      };
    case 'rejected': {
      const note = i.approvalNote ? ` — “${clip(i.approvalNote)}”` : '';
      return {
        ...base,
        state: 'active',
        fact: `Changes requested${note}`,
        actor: i.decidedByName ? `Sent back by ${i.decidedByName}` : undefined,
        at: shortDate(i.decidedAt) || undefined,
        action: { label: 'Revise and resubmit', to: null },
      };
    }
    default:
      return {
        ...base,
        state: 'todo',
        fact: `Not submitted · ${budget ? `budget ${budget}` : 'no budget set'}`,
        action: { label: 'Review budget and submit', to: null },
      };
  }
}

/* ── Stage 4 ────────────────────────────────────────────────────────────── */

function goLiveStage(i: OpeningSetupInput, processDone: boolean, approvalDone: boolean): OpeningStage {
  const base = { key: 'golive', title: 'Go live' } as const;
  if (i.publicApplyEnabled && i.status === 'open' && i.hasPublishedWorkflow) {
    return { ...base, state: 'done', fact: 'Accepting applications' };
  }
  const missing: string[] = [];
  if (!processDone) missing.push('a live hiring process (stage 2)');
  if (!approvalDone) missing.push('budget approval (stage 3)');
  if (missing.length > 0) {
    return {
      ...base,
      state: 'blocked',
      fact: 'Not live',
      reason: `Needs ${missing.join(' and ')}.`,
    };
  }
  if (i.publicApplyEnabled && i.status !== 'open') {
    return {
      ...base,
      state: 'active',
      fact: `Switched on, but the opening is ${i.status}`,
      action: { label: 'Reopen the opening', to: null },
    };
  }
  if (i.publishAt) {
    return { ...base, state: 'awaiting', fact: `Scheduled to open on ${shortDate(i.publishAt)}` };
  }
  return {
    ...base,
    state: 'todo',
    fact: i.status === 'open' ? 'Ready — applications are off' : `Opening is ${i.status}`,
    action: { label: 'Open to applications', to: null },
  };
}

/* ── Stage 5 ────────────────────────────────────────────────────────────── */

function documentsStage(i: OpeningSetupInput): OpeningStage {
  const n = i.documentCount;
  return {
    key: 'documents',
    title: 'Preboarding documents',
    state: 'optional',
    fact:
      n === undefined
        ? undefined
        : n === 0
          ? 'None required'
          : `${n} document${n === 1 ? '' : 's'} listed`,
  };
}

/**
 * The stages of the setup rail. The read-only (super admin) viewer gets stages
 * 2 to 4: stages 1 and 5 need HR-only endpoints, so showing them would mean
 * guessing.
 */
export function openingStages(i: OpeningSetupInput): OpeningStage[] {
  const process = processStage(i);
  const approval = approvalStage(i);
  const goLive = goLiveStage(i, process.state === 'done', approval.state === 'done');
  if (i.viewer === 'readonly') return [process, approval, goLive];
  return [roleStage(i), process, approval, goLive, documentsStage(i)];
}

/**
 * The stage the user should work on next: the first, in order, that is not
 * done, not waiting on someone else and not blocked. Null when there is none —
 * and while stage 1 is still loading, so the answer does not flip afterwards.
 */
export function nextStageKey(stages: readonly OpeningStage[]): StageKey | null {
  if (stages.some((s) => s.loading)) return null;
  const next = stages.find((s) => s.key !== 'documents' && (s.state === 'todo' || s.state === 'active'));
  return next?.key ?? null;
}

/** Which stage the rail opens on: the next one, else one that is waiting, else the last that matters. */
export function defaultStageKey(stages: readonly OpeningStage[]): StageKey {
  // While the first stage is still loading, stay on it rather than flashing
  // another stage's panel (and its requests) and jumping back.
  if (stages.some((s) => s.loading)) return stages[0]?.key ?? 'process';
  const next = nextStageKey(stages);
  if (next) return next;
  const awaiting = stages.find((s) => s.state === 'awaiting');
  if (awaiting) return awaiting.key;
  return stages.some((s) => s.key === 'golive') ? 'golive' : (stages[0]?.key ?? 'process');
}

export interface PrimaryAction extends StageAction {
  stageKey: StageKey;
}

/** The header's single primary while setup is unfinished; null when nothing is left to do. */
export function primaryAction(stages: readonly OpeningStage[]): PrimaryAction | null {
  const key = nextStageKey(stages);
  if (!key) return null;
  const action = stages.find((s) => s.key === key)?.action;
  return action ? { ...action, stageKey: key } : null;
}
