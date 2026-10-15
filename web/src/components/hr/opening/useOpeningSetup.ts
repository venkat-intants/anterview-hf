// useOpeningSetup — gathers what the setup rail needs and hands it to the pure
// `openingStages()`.
//
// REQUEST DISCIPLINE
// The DB is billed by wake-ups, and only the dashboard query polls. Everything
// here is fetched ONLY while `enabled` (the Setup tab is open), never polls, and
// asks for no more than the stage states need:
//
//   • the opening record      — fresher than the dashboard's copy after a panel save
//   • JD history              — stage 1
//   • workflow list           — only when the dashboard says a version exists
//   • workflow detail         — the draft if there is one, else the live version
//   • validation, simulation  — only for a draft still being authored, because
//                               they decide nothing else
//   • publish schedule        — stage 4
//   • document requirements   — stage 5
//
// Query keys are shared with the pages that own those resources, so a request
// made here is a cache hit there.

import { useQuery } from '@tanstack/react-query';
import {
  getJdHistory,
  getPublishSchedule,
  getRequisition,
  type Requisition,
  type RequisitionDashboard,
} from '@/api/requisitions';
import { getWorkflow, listWorkflows, validateWorkflow } from '@/api/workflows';
import { getSimulation } from '@/api/workflowReview';
import { listDocumentRequirements } from '@/api/offers';
import { queryKeys } from '@/lib/queryKeys';
import { jdFacts, openingStages, type OpeningStage, type WorkflowFacts } from '@/lib/openingSetup';

interface Args {
  requisitionId: string;
  /** The (polled) dashboard payload, once it has loaded. */
  dashboard: RequisitionDashboard | undefined;
  dashboardUpdatedAt: number;
  /** True only while the Setup tab is open. */
  enabled: boolean;
  readOnly: boolean;
}

interface Result {
  /** The freshest copy of the opening: the record query after a panel save, else the dashboard's. */
  requisition: Requisition | undefined;
  stages: OpeningStage[];
  /**
   * Every fact that decides which stage the rail opens on has arrived (or
   * failed, which counts: an error will not change the answer by itself). Until
   * then the caller must not mount a stage's panel, because the stage it would
   * mount may be the wrong one and its requests would be wasted.
   */
  ready: boolean;
}

export function useOpeningSetup({
  requisitionId,
  dashboard,
  dashboardUpdatedAt,
  enabled,
  readOnly,
}: Args): Result {
  const on = enabled && !readOnly && Boolean(requisitionId);

  const record = useQuery({
    queryKey: queryKeys.requisition(requisitionId),
    queryFn: () => getRequisition(requisitionId),
    enabled: on,
  });
  const jd = useQuery({
    queryKey: queryKeys.jdHistory(requisitionId),
    queryFn: () => getJdHistory(requisitionId),
    enabled: on,
  });
  const schedule = useQuery({
    queryKey: queryKeys.publishSchedule(requisitionId),
    queryFn: () => getPublishSchedule(requisitionId),
    enabled: on,
  });
  const docs = useQuery({
    queryKey: queryKeys.requisitionDocumentRequirements(requisitionId),
    queryFn: () => listDocumentRequirements(requisitionId),
    enabled: on,
  });

  const publishedV = dashboard?.workflow_state.published_version ?? null;
  const draftV = dashboard?.workflow_state.draft_version ?? null;
  const anyWorkflow = publishedV !== null || draftV !== null;

  const versions = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId),
    enabled: on && anyWorkflow,
  });
  const list = versions.data ?? [];
  const focusId =
    list.find((w) => w.status === 'draft')?.id ??
    list.find((w) => w.status === 'published')?.id ??
    null;

  const wf = useQuery({
    queryKey: ['hr', 'workflow', focusId],
    queryFn: () => getWorkflow(focusId as string),
    enabled: on && Boolean(focusId),
  });
  const w = wf.data;
  const authoring =
    w?.status === 'draft' &&
    (w.review_status === 'draft' || w.review_status === 'changes_requested');
  const validation = useQuery({
    queryKey: ['hr', 'workflow', focusId, 'validate'],
    queryFn: () => validateWorkflow(focusId as string),
    enabled: on && authoring && (w?.rounds.length ?? 0) > 0,
  });
  const simulation = useQuery({
    queryKey: ['hr', 'workflow', focusId, 'simulation'],
    queryFn: () => getSimulation(focusId as string),
    enabled: on && authoring && (w?.rounds.length ?? 0) > 0,
  });

  const settled = (q: { isSuccess: boolean; isError: boolean }): boolean =>
    q.isSuccess || q.isError;
  const needsChecks = authoring && (w?.rounds.length ?? 0) > 0;

  let workflow: WorkflowFacts | null | undefined;
  if (!anyWorkflow) {
    workflow = null;
  } else if (w && (!needsChecks || (settled(validation) && settled(simulation)))) {
    workflow = {
      version: w.version,
      status: w.status,
      reviewStatus: w.review_status,
      rounds: w.rounds.map((r) => ({
        kind: r.kind,
        needsQuestions: r.needs_questions,
        examRoundId: r.exam_round_id,
      })),
      simulated: Boolean(simulation.data) && !simulation.data?.stale,
      publishable: validation.data?.publishable ?? false,
    };
  }

  // The record query is not polled, so prefer it only when it is newer than the
  // dashboard's own copy.
  const requisition =
    record.data && record.dataUpdatedAt >= dashboardUpdatedAt
      ? record.data
      : dashboard?.requisition;

  if (!dashboard || !requisition) return { requisition, stages: [], ready: false };

  // The read-only viewer has no stage 1 or 5 and fetches nothing here, so its
  // stages (built from the dashboard alone) are final as they stand.
  const jdSettled = readOnly || jd.isSuccess || jd.isError;
  const workflowSettled =
    readOnly ||
    !anyWorkflow ||
    workflow !== undefined ||
    versions.isError ||
    wf.isError ||
    // The list answered but holds nothing to open: there is no detail to wait for.
    (versions.isSuccess && focusId === null);
  const ready = on ? jdSettled && workflowSettled : readOnly;

  let stages = openingStages({
    requisitionId,
    viewer: readOnly ? 'readonly' : 'hr',
    status: requisition.status,
    publicApplyEnabled: requisition.public_apply_enabled,
    approvalStatus: requisition.approval_status,
    submittedByName: requisition.submitted_by_name,
    submittedAt: requisition.submitted_for_approval_at,
    decidedByName: requisition.approval_decided_by_name,
    decidedAt: requisition.approval_decided_at,
    approvalNote: requisition.approval_note,
    budget: {
      amount: requisition.budget_amount,
      currency: requisition.budget_currency,
      basis: requisition.budget_basis,
      period: requisition.budget_period,
    },
    publishedWorkflowVersion: publishedV,
    draftWorkflowVersion: draftV,
    hasPublishedWorkflow: dashboard.has_published_workflow,
    workflow: readOnly ? undefined : workflow,
    jd: jd.data ? jdFacts(jd.data) : undefined,
    publishAt: schedule.data ? schedule.data.publish_at : undefined,
    documentCount: docs.data?.length,
  });

  // A failed JD query must not leave the first stage claiming "checking" for ever.
  if (jd.isError) {
    stages = stages.map((s) =>
      s.key === 'role'
        ? { ...s, fact: 'Could not load the job description status', loading: true }
        : s,
    );
  }
  return { requisition, stages, ready };
}
