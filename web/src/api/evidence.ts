// evidence.ts — the evidence graph (PH5-E5): every declared piece of
// evidence one application has, and what existed when one hire/reject
// decision was recorded. Field names match
// services/data_gateway/app/schemas/evidence.py EXACTLY.
//
// READ-ONLY, HR-manager only. Nothing here writes anything; both routes 404
// — never 403 — on a wrong company or a non-existent id, the same
// non-disclosure shape every other company-scoped GET in this console has.
//
// HONEST SEMANTICS, non-negotiable (carried through from the server):
// `available_at_decision` means only that a node existed, by timestamp, when
// a decision was recorded — never that the decision-maker read or relied on
// it. Nothing here is ever called "supported by".

import { apiGet } from './client';
import { pathId } from './pathId';

// ---------------------------------------------------------------------------
// Closed vocabularies
// ---------------------------------------------------------------------------

export type EvidenceStage =
  | 'application'
  | 'screening'
  | 'assessment'
  | 'interview'
  | 'offer'
  | 'decision';

export type EvidenceNodeKind =
  | 'application'
  | 'screening_ats'
  | 'screening_answers'
  | 'stage_move'
  | 'exam_attempt'
  | 'round_result'
  | 'ai_interview'
  | 'interview_session'
  | 'human_scorecard'
  | 'task_submission'
  | 'offer'
  | 'decision';

export type EvidenceEdgeKind =
  | 'part_of'
  | 'originates_from'
  | 'supersedes'
  | 'available_at_decision'
  | 'follows_decision';

export type ProducedBy = 'human' | 'ai' | 'candidate' | 'system';

export type Attribution = 'direct' | 'inferred_single_application';

export type Lifecycle =
  | 'live'
  | 'superseded'
  | 'withdrawn'
  | 'redacted'
  | 'purged'
  | 'consent_withdrawn';

// ---------------------------------------------------------------------------
// Shared shapes
// ---------------------------------------------------------------------------

export interface EvidenceStageInfo {
  name: EvidenceStage;
  round_id: string | null;
  round_title: string | null;
  round_kind: string | null;
  workflow_version: number | null;
}

export interface EvidenceSourceRef {
  table: string;
  id: string;
}

export interface EvidenceActorRef {
  user_id: string | null;
  name: string | null;
  role: string | null;
}

export interface EvidenceProvenance {
  produced_by: ProducedBy;
  actor: EvidenceActorRef | null;
  method: string;
  attribution: Attribution;
}

/** A node's `content` shape varies by `kind` (see evidence_graph.py's node
 *  builders); this console renders it defensively by kind rather than
 *  assuming every field is present. `null` when `content_hidden_reason` is set. */
export type EvidenceNodeContent = Record<string, unknown>;

export interface EvidenceNode {
  id: string;
  kind: EvidenceNodeKind;
  stage: EvidenceStageInfo;
  source: EvidenceSourceRef;
  provenance: EvidenceProvenance;
  occurred_at: string;
  recorded_at: string;
  lifecycle: Lifecycle;
  content: EvidenceNodeContent | null;
  content_hidden_reason: string | null;
  href: string;
}

export interface EvidenceEdge {
  from: string;
  to: string;
  kind: EvidenceEdgeKind;
}

export interface OmittedItem {
  kind: string;
  reason: string;
}

export interface EvidenceScope {
  company_id: string;
  enrolment_id: string;
  applicant_id: string;
}

export interface EvidenceCandidateRef {
  name: string;
  erased: boolean;
}

export interface EvidenceRequisitionRef {
  id: string | null;
  title: string | null;
}

export interface EvidenceWorkflowRef {
  id: string | null;
  version: number | null;
}

export interface DecisionSummary {
  id: number;
  outcome: 'hired' | 'rejected';
  decided_at: string;
}

export interface EvidenceGraph {
  schema_version: number;
  generated_at: string;
  scope: EvidenceScope;
  candidate: EvidenceCandidateRef;
  requisition: EvidenceRequisitionRef;
  workflow: EvidenceWorkflowRef;
  nodes: EvidenceNode[];
  edges: EvidenceEdge[];
  decisions: DecisionSummary[];
  omitted: OmittedItem[];
}

export function getEvidenceGraph(enrolmentId: string): Promise<EvidenceGraph> {
  return apiGet<EvidenceGraph>(`/hr/enrolments/${pathId(enrolmentId)}/evidence-graph`);
}

// ---------------------------------------------------------------------------
// Decision trace — what existed when ONE decision was recorded.
// ---------------------------------------------------------------------------

export interface TracePathStep {
  node_id: string;
  edge: EvidenceEdgeKind | null;
}

export interface TraceEvidenceItem {
  node: EvidenceNode;
  path: TracePathStep[];
  /** A short reason when this node's CONTENT is known to differ from what it
   *  was at decision time (a re-score, a correction opened later) — never
   *  silently folded into an unqualified "available" claim. */
  changed_after_decision: string | null;
}

export interface AfterDecisionItem {
  node: EvidenceNode;
  reason: string;
}

export interface DecisionDetail {
  id: number;
  enrolment_id: string;
  outcome: 'hired' | 'rejected';
  reversal: boolean;
  decided_at: string;
  decided_by: EvidenceActorRef;
  automated: boolean;
  reason_code: string | null;
  reason_label: string | null;
  reason: string | null;
}

export interface OtherDecisionRef {
  id: number;
  outcome: 'hired' | 'rejected';
  decided_at: string;
  reversal: boolean;
}

export interface AiInvolvement {
  ai_produced_evidence: number;
  /** Always "human" — the route 404s on anything else (D-05, structural). */
  decided_by: 'human';
}

export interface DecisionTrace {
  schema_version: number;
  decision: DecisionDetail;
  other_decisions: OtherDecisionRef[];
  evidence: TraceEvidenceItem[];
  after_decision: AfterDecisionItem[];
  edges: EvidenceEdge[];
  by_stage: Record<string, number>;
  ai_involvement: AiInvolvement;
  omitted: OmittedItem[];
}

export function getDecisionTrace(decisionId: number): Promise<DecisionTrace> {
  return apiGet<DecisionTrace>(`/hr/decisions/${decisionId}/trace`);
}

// ---------------------------------------------------------------------------
// Display helpers — stage order and badges, shared between the graph and the
// trace screens.
// ---------------------------------------------------------------------------

/** The order stages are grouped in on screen — mirrors the funnel order the
 *  rest of the HR console already uses. */
export const STAGE_ORDER: EvidenceStage[] = [
  'application',
  'screening',
  'assessment',
  'interview',
  'offer',
  'decision',
];

export const STAGE_LABELS: Record<EvidenceStage, string> = {
  application: 'Application',
  screening: 'Screening',
  assessment: 'Assessment',
  interview: 'Interview',
  offer: 'Offer',
  decision: 'Decision',
};

export const PRODUCED_BY_LABELS: Record<ProducedBy, string> = {
  human: 'Human',
  ai: 'AI',
  candidate: 'Candidate',
  system: 'System',
};
